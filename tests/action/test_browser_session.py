"""Tests for action/tools/browser_session.py. No real browser, registry or network:
the opener and every page are fakes."""
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from desktop_ai_agent.action.tools import browser_session as bs

PwTimeout = type("TimeoutError", (Exception,), {"__module__": "playwright._impl._errors"})


class FakePage:
    def __init__(self, url="about:blank", fail=None):
        self.url, self.closed, self.fail = url, False, fail

    def is_closed(self):
        return self.closed

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        if self.fail:
            raise self.fail


class FakeContext:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self):
        self.up = True

    def is_connected(self):
        return self.up


def live(pages=None):
    return bs.Live(None, FakeBrowser(), FakeContext(list(pages or [])), bs.BrowserInfo("x.exe", "opera"), 42)


class TestHelpers(unittest.TestCase):
    def test_exe_from_command(self):
        self.assertEqual(bs.exe_from_command('"C:\\B\\opera.exe" -noautoupdate -- "%1"'), "C:\\B\\opera.exe")
        self.assertEqual(bs.exe_from_command("C:\\B\\chrome.exe %1"), "C:\\B\\chrome.exe")
        self.assertEqual(bs.exe_from_command(""), "")

    def test_chromium_browsers_accepted(self):
        self.assertEqual(bs.check_browser("C:\\x\\Opera.exe").program, "opera")
        self.assertEqual(bs.check_browser("C:\\x\\msedge.exe").program, "msedge")

    def test_firefox_and_none_refused_without_fallback(self):
        with self.assertRaisesRegex(bs.BrowserError, "not Chromium-based"):
            bs.check_browser("C:\\x\\firefox.exe")
        with self.assertRaisesRegex(bs.BrowserError, "No default web browser"):
            bs.check_browser("")

    def test_one_profile_per_browser_outside_the_repo(self):
        root = Path("R")
        profile, session = bs.paths_for(bs.BrowserInfo("x", "opera"), root)
        self.assertEqual(profile, root / "opera" / "profile")
        self.assertEqual(session, root / "opera" / "session.json")

    def test_session_file_round_trip_and_bad_files(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "session.json"
            self.assertIsNone(bs.read_session(path))
            bs.write_session(path, {"port": 9222, "pid": 7})
            self.assertEqual(bs.read_session(path), {"port": 9222, "pid": 7})
            path.write_text('{"port": "x"}', encoding="utf-8")
            self.assertIsNone(bs.read_session(path))
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(bs.read_session(path))


class TestLive(unittest.TestCase):
    def test_current_page_falls_back_to_the_newest_open_tab(self):
        a, b = FakePage("https://a"), FakePage("https://b")
        lv = live([a, b])
        self.assertIs(lv.page(), b)
        lv.set_current(a)
        self.assertIs(lv.page(), a)
        a.closed = True
        self.assertIs(lv.page(), b)
        b.closed = True
        self.assertIsNone(lv.page())

    def test_keys_are_stable_per_tab(self):
        a, b = FakePage("https://a"), FakePage("https://b")
        lv = live([a, b])
        ka, kb = lv.key(a), lv.key(b)
        self.assertNotEqual(ka, kb)
        self.assertEqual(lv.key(a), ka)
        self.assertIs(lv.page_by_key(kb), b)
        b.closed = True
        self.assertIsNone(lv.page_by_key(kb))

    def test_follow_new_switches_to_a_tab_a_click_opened(self):
        a = FakePage("https://a")
        lv = live([a])
        before = lv.pages()
        self.assertIsNone(lv.follow_new(before))
        new = lv.context.new_page()
        self.assertIs(lv.follow_new(before), new)
        self.assertIs(lv.page(), new)

    def test_new_tab_reuses_the_blank_start_tab_once(self):
        blank = FakePage("about:blank")
        lv = live([blank])
        page, loaded = lv.new_tab("https://youtube.com")
        self.assertIs(page, blank)
        self.assertTrue(loaded)
        second, _ = lv.new_tab("https://github.com")
        self.assertIsNot(second, blank)
        self.assertEqual(len(lv.context.pages), 2)
        self.assertIs(lv.page(), second)

    def test_new_tab_reuses_a_left_over_blank_tab(self):
        corner, blank = FakePage("https://gxcorner.games/"), FakePage("about:blank")
        lv = live([corner, blank])  # Opera's first start: its start page + our blank tab
        page, _ = lv.new_tab("https://youtube.com")
        self.assertIs(page, blank)
        self.assertEqual(len(lv.context.pages), 2)

    def test_new_tab_timeout_keeps_the_loading_page(self):
        lv = live([FakePage("https://a")])
        lv.context.new_page = lambda: lv.context.pages.append(FakePage(fail=PwTimeout("slow"))) or lv.context.pages[-1]
        page, loaded = lv.new_tab("https://slow.example")
        self.assertFalse(loaded)
        self.assertEqual(page.url, "https://slow.example")


class TestSession(unittest.TestCase):
    def test_work_runs_on_one_browser_thread_and_returns_plain_data(self):
        opened = []

        def opener(launch, old):
            opened.append((launch, old))
            return live([FakePage("https://a")])

        session = bs.BrowserSession(opener=opener)
        threads = {session.call(lambda lv: threading.current_thread().name) for _ in range(3)}
        self.assertEqual(threads, {"agent-browser"})
        self.assertEqual(len(opened), 1)  # connected once, then reused
        self.assertEqual(session.call(lambda lv: lv.page().url), "https://a")
        self.assertEqual(session.known_pid(), (42, "opera"))

    def test_errors_reach_the_caller(self):
        session = bs.BrowserSession(opener=lambda launch, old: live())
        with self.assertRaisesRegex(LookupError, "boom"):
            session.call(lambda lv: (_ for _ in ()).throw(LookupError("boom")))
        self.assertEqual(session.call(lambda lv: 1), 1)  # the worker survives an error

    def test_reconnects_when_the_browser_was_closed(self):
        made = []

        def opener(launch, old):
            made.append(old)
            return live()

        session = bs.BrowserSession(opener=opener)
        first = session.call(lambda lv: lv)
        first.browser.up = False
        second = session.call(lambda lv: lv)
        self.assertIsNot(first, second)
        self.assertIs(made[1], first)  # the old one is passed on (its Playwright is reused)

    def test_launch_false_is_passed_to_the_opener(self):
        def opener(launch, old):
            if not launch:
                raise bs.NotRunning("not open")
            return live()

        session = bs.BrowserSession(opener=opener)
        with self.assertRaises(bs.NotRunning):
            session.call(lambda lv: 1, launch=False)
        self.assertEqual(session.call(lambda lv: 1), 1)

    def test_timeout_says_so(self):
        gate = threading.Event()
        session = bs.BrowserSession(opener=lambda launch, old: live())
        with self.assertRaisesRegex(TimeoutError, "did not answer"):
            session.call(lambda lv: gate.wait(2), timeout=0.05)
        gate.set()

    def test_known_pid_unknown_without_a_default_browser(self):
        self.assertEqual(bs.BrowserSession(opener=lambda launch, old: live()).known_pid(), (0, ""))


if __name__ == "__main__":
    unittest.main()
