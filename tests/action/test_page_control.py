"""Tests for action/tools/page_control.py. The browser session, every page, the window
list and the address bar are fakes: no real browser or window is touched."""
import unittest
from unittest import mock

from desktop_ai_agent.action.tools import browser_session as bs
from desktop_ai_agent.action.tools import page_control as pc
from desktop_ai_agent.input.page_elements import PageElement
from desktop_ai_agent.input.windows import WindowInfo

PwTimeout = type("TimeoutError", (Exception,), {"__module__": "playwright._impl._errors"})
PwError = type("Error", (Exception,), {"__module__": "playwright._impl._errors"})

SNAP = """- combobox "Search" [ref=e3] [box=300,10,400,30]
- button "Search" [ref=e4] [cursor=pointer] [box=700,10,40,30]
- link "lofi hip hop radio" [ref=e6] [cursor=pointer] [box=10,900,300,40]
- button "Accept all" [ref=e9] [box=10,500,100,30]
"""


class FakeKeyboard:
    def __init__(self, log):
        self.log = log

    def press(self, key):
        self.log.append(("key", key))

    def insert_text(self, text):
        self.log.append(("insert", text))


class FakeLocator:
    def __init__(self, page, ref):
        self.page, self.ref = page, ref

    def count(self):
        if self.page.count_raises:
            raise PwError("stale ref")
        return 1 if self.ref in self.page.refs else 0

    def scroll_into_view_if_needed(self, timeout=None):
        self.page.log.append(("scroll", self.ref))

    def click(self, button="left", timeout=None):
        if self.page.click_timeout:
            raise PwTimeout("Timeout 5000ms exceeded.\nCall log:\n  - waiting for element")
        self.page.log.append(("click", self.ref, button))
        if self.ref in self.page.opens_tab:
            self.page.context.new_page().url = self.page.opens_tab[self.ref]

    def dblclick(self, button="left", timeout=None):
        self.page.log.append(("dblclick", self.ref, button))

    def fill(self, text, timeout=None):
        self.page.log.append(("fill", self.ref, text))

    def press(self, key, timeout=None):
        self.page.log.append(("press", self.ref, key))

    def evaluate(self, js, arg=None):
        if js == pc._FIELD_JS:
            return self.page.fields.get(self.ref, {"kind": "input", "empty": True})
        self.page.log.append(("highlight", self.ref, arg))


class FakePage:
    def __init__(self, context, url="https://www.youtube.com/", title="YouTube", snapshot=SNAP):
        self.context, self.url, self._title, self.snapshot = context, url, title, snapshot
        self.refs = {"e3", "e4", "e6", "e9"}
        self.closed = False
        self.log = []
        self.fields = {}
        self.opens_tab = {}
        self.count_raises = False
        self.click_timeout = False
        self.text = "Welcome. Settings. Delete account is under Settings."
        self.back_url = None
        self.leave_prompt = False
        self.keyboard = FakeKeyboard(self.log)

    def is_closed(self):
        return self.closed

    def title(self):
        return self._title

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def aria_snapshot(self, mode=None, boxes=None, timeout=None):
        self.log.append(("snapshot", mode, boxes))
        return self.snapshot

    def evaluate(self, js):
        return 800

    def inner_text(self, selector, timeout=None):
        return self.text

    def locator(self, selector):
        assert selector.startswith("aria-ref=")
        return FakeLocator(self, selector[len("aria-ref="):])

    def bring_to_front(self):
        self.log.append(("front",))

    def wait_for_load_state(self, state, timeout=None):
        self.log.append(("load", state))

    def go_back(self, wait_until=None, timeout=None):
        if self.back_url:
            self.url, self.back_url = self.back_url, None

    def go_forward(self, wait_until=None, timeout=None):
        return None

    def reload(self, wait_until=None, timeout=None):
        self.log.append(("reload",))

    def close(self, run_before_unload=False):
        self.closed = not self.leave_prompt

    def wait_for_event(self, name, timeout=None):
        raise PwTimeout("still open")


class FakeContext:
    def __init__(self):
        self.pages = []

    def new_page(self):
        page = FakePage(self, url="about:blank", title="New tab", snapshot="")
        self.pages.append(page)
        return page


class FakeSession:
    def __init__(self, live):
        self.live = live
        self.launches = []

    def call(self, fn, *, launch=True, timeout=None):
        self.launches.append(launch)
        if self.live is None:
            raise bs.NotRunning("not open")
        return fn(self.live)

    def known_pid(self):
        return (self.live.pid, "opera") if self.live else (0, "")


AGENT = WindowInfo(500, "YouTube - Opera", "opera", 42)
USERS = WindowInfo(600, "Inbox - Opera", "opera", 77)
CODE = WindowInfo(700, "main.py - VS Code", "Code", 88)


class Case(unittest.TestCase):
    def setUp(self):
        self.context = FakeContext()
        self.page = FakePage(self.context)
        self.context.pages.append(self.page)
        browser = mock.Mock()
        browser.is_connected.return_value = True
        self.live = bs.Live(None, browser, self.context, bs.BrowserInfo("opera.exe", "opera"), 42)
        self.session = FakeSession(self.live)
        self.windows = [AGENT, USERS, CODE]
        self.address = {600: "https://mail.google.com/mail/u/0/#inbox"}
        self.fronted = []
        for name, value in {
            "_session": lambda: self.session,
            "_windows": lambda: list(self.windows),
            "_address_of": lambda hwnd: self.address.get(hwnd),
            "_bring_to_front": lambda hwnd: self.fronted.append(hwnd) or True,
        }.items():
            patcher = mock.patch.object(pc, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pc.reset()
        self.addCleanup(pc.reset)

    def element(self, ref, name="x", kind="Button", key=None):
        return PageElement(1, name, kind, "button", ref, page_key=key or self.live.key(self.page))


class TestRules(unittest.TestCase):
    def test_yes_and_no(self):
        for text in ("yes", "ok go ahead", "Proceed please", "no problem, continue", "accept them"):
            self.assertTrue(pc.says_yes(text), text)
        for text in ("no", "don't", "stop", "yes but no", "reject", ""):
            self.assertFalse(pc.says_yes(text), text)
        self.assertTrue(pc.says_no("reject all"))
        self.assertFalse(pc.says_no("no worries"))

    def test_consent_buttons(self):
        self.assertEqual(pc.consent_choice("Accept all"), "accept")
        self.assertEqual(pc.consent_choice("Allow all cookies"), "accept")
        self.assertEqual(pc.consent_choice("I agree"), "accept")
        self.assertEqual(pc.consent_choice("Reject all"), "reject")
        self.assertIsNone(pc.consent_choice("Search"))
        self.assertIsNone(pc.consent_choice("Read the cookie policy"))

    def test_consent_click_needs_the_users_answer(self):
        with self.assertRaisesRegex(PermissionError, "cookie"):
            pc.check_consent_click("Accept all", "open youtube and play lofi")
        pc.check_consent_click("Accept all", "yes accept")
        pc.check_consent_click("Reject all", "no, reject them")
        with self.assertRaises(PermissionError):
            pc.check_consent_click("Reject all", "yes")
        pc.check_consent_click("Search", "")  # ordinary buttons are not affected

    def test_private_hosts(self):
        self.assertTrue(pc.is_private("mail.google.com"))
        self.assertTrue(pc.is_private("docs.google.com"))
        self.assertFalse(pc.is_private("www.google.com"))
        self.assertFalse(pc.is_private("youtube.com"))


class TestCloudWarning(Case):
    def test_local_model_never_asks(self):
        self.page.url = "https://mail.google.com/mail/u/0/"
        pc.list_page("open my email", cloud=False)

    def test_cloud_model_asks_once_then_the_ok_lasts_until_new_chat(self):
        self.page.url = "https://mail.google.com/mail/u/0/"
        with self.assertRaisesRegex(PermissionError, "cloud"):
            pc.list_page("open my email", cloud=True)
        self.assertNotIn(("snapshot", "ai", True), self.page.log)  # nothing was read
        with self.assertRaises(PermissionError):
            pc.list_page("what is this", cloud=True)  # not an answer: still blocked
        pc.list_page("yes proceed", cloud=True)
        pc.read(request="summarize", cloud=True)  # same site, same chat: no new question
        pc.reset()  # New chat
        with self.assertRaises(PermissionError):
            pc.read(request="yes", cloud=True)  # a yes before the question does not count

    def test_other_sites_are_read_freely(self):
        pc.list_page("search", cloud=True)


class TestListPage(Case):
    def test_lists_ranked_with_hints_and_remembers_the_tab(self):
        shown, result = pc.list_page("play lofi")
        self.assertEqual(result["area"], "page")
        self.assertEqual(result["page"], "YouTube")
        self.assertEqual(result["site"], "www.youtube.com")
        self.assertEqual(result["elements"].splitlines()[0], '3: Hyperlink "lofi hip hop radio" (below)')
        self.assertEqual({e.page_key for e in shown}, {self.live.key(self.page)})
        self.assertIn(("snapshot", "ai", True), self.page.log)

    def test_empty_page_says_it_may_be_loading(self):
        self.page.snapshot = ""
        shown, result = pc.list_page()
        self.assertEqual(shown, [])
        self.assertIn("still be loading", result["note"])

    def test_no_page_open(self):
        self.context.pages.clear()
        with self.assertRaisesRegex(LookupError, "web_open"):
            pc.list_page()


class TestHandOver(Case):
    def test_users_browser_page_opens_in_the_agent_browser(self):
        _shown, result = pc.list_page("", handover_hwnd=600)
        self.assertIn("user's own browser", result["note"])
        self.assertEqual(self.live.page().url, "https://mail.google.com/mail/u/0/#inbox")
        self.assertEqual(self.fronted, [500])  # the agent's window, never the user's

    def test_an_open_tab_with_the_same_address_is_reused(self):
        self.page.url = "https://mail.google.com/mail/u/0/#inbox"
        pc.hand_over(600)
        self.assertEqual(len(self.context.pages), 1)

    def test_no_web_address(self):
        self.address = {}
        self.assertIsNone(pc.hand_over(600))
        with self.assertRaises(pc.HandOverFailed):
            pc.list_page("", handover_hwnd=600, strict=True)
        _shown, result = pc.list_page("", handover_hwnd=600)  # not strict: the agent's tab
        self.assertEqual(result["page"], "YouTube")

    def test_browser_kind(self):
        self.assertEqual(pc.browser_kind(500), "agent")
        self.assertEqual(pc.browser_kind(600), "user")
        self.assertIsNone(pc.browser_kind(700))
        self.assertIsNone(pc.browser_kind(None))
        self.assertIsNone(pc.browser_kind(999))


class TestActions(Case):
    def test_click_scrolls_clicks_and_waits(self):
        result = pc.click(self.element("e4", "Search"))
        self.assertEqual(result["clicked"], "Search")
        self.assertEqual(self.page.log[:2], [("scroll", "e4"), ("click", "e4", "left")])
        self.assertIn(("load", "domcontentloaded"), self.page.log)
        self.assertNotIn("note", result)

    def test_double_and_right_click(self):
        pc.click(self.element("e4"), button="right", double=True)
        self.assertIn(("dblclick", "e4", "right"), self.page.log)

    def test_a_new_tab_becomes_current(self):
        self.page.opens_tab = {"e6": "https://www.youtube.com/watch?v=1"}
        result = pc.click(self.element("e6", "lofi"))
        self.assertIn("new tab", result["note"])
        self.assertEqual(self.live.page().url, "https://www.youtube.com/watch?v=1")

    def test_stale_or_missing_element(self):
        with self.assertRaisesRegex(LookupError, "no longer on the page"):
            pc.click(self.element("e99"))
        self.page.count_raises = True
        with self.assertRaisesRegex(LookupError, "list_elements again"):
            pc.click(self.element("e4"))

    def test_closed_tab(self):
        with self.assertRaisesRegex(LookupError, "closed"):
            pc.click(self.element("e4", key=999))

    def test_covered_element_gives_a_short_error(self):
        self.page.click_timeout = True
        with self.assertRaisesRegex(LookupError, "hidden or covered"):
            pc.click(self.element("e4"))

    def test_other_playwright_errors_are_one_line(self):
        def boom(live):
            raise PwError("Target closed\nCall log:\n  - a\n  - b")

        with self.assertRaisesRegex(RuntimeError, r"^Browser: Target closed$"):
            pc._call(boom)

    def test_type_fills_inputs_and_submits_searches(self):
        self.page.fields["e3"] = {"kind": "input", "search": True}
        result = pc.type_into(self.element("e3", "Search", "ComboBox"), "lofi", submit=True)
        self.assertIn(("fill", "e3", "lofi"), self.page.log)
        self.assertIn(("press", "e3", "Enter"), self.page.log)
        self.assertIn("search ran", result["note"])

    def test_submit_only_in_search_fields(self):
        self.page.fields["e3"] = {"kind": "input", "search": False}
        with self.assertRaisesRegex(ValueError, "only in search fields"):
            pc.type_into(self.element("e3", "Name", "Edit"), "Ann", submit=True)
        self.assertNotIn(("fill", "e3", "Ann"), self.page.log)
        pc.type_into(self.element("e3", "Query", "SearchBox"), "x", submit=True)  # a searchbox always may

    def test_rich_editor_text_goes_at_the_end(self):
        self.page.fields["e3"] = {"kind": "editable", "empty": False}
        result = pc.type_into(self.element("e3", "Message", "Edit"), "hello")
        self.assertEqual(self.page.log[-2:], [("key", "Control+End"), ("insert", "hello")])
        self.assertIn("not pressed", result["note"])

    def test_never_types_passwords(self):
        self.page.fields["e3"] = {"kind": "input", "password": True}
        with self.assertRaisesRegex(PermissionError, "password"):
            pc.type_into(self.element("e3", "Password", "Edit"), "hunter2")
        self.assertFalse(any(entry[0] == "fill" for entry in self.page.log))

    def test_highlight_outlines_and_shows_the_window(self):
        result = pc.highlight(self.element("e4", "Search"))
        self.assertIn(("highlight", "e4", pc.HIGHLIGHT_MS), self.page.log)
        self.assertEqual(result["highlighted"], "Search")
        self.assertEqual(self.fronted, [500])

    def test_go_back_and_nowhere_to_go(self):
        self.page.back_url = "https://www.youtube.com/results"
        self.assertNotIn("note", pc.go("back"))
        self.assertEqual(self.page.url, "https://www.youtube.com/results")
        self.assertIn("no page to go forward", pc.go("forward")["note"])
        self.assertNotIn("note", pc.go("reload"))
        with self.assertRaises(ValueError):
            pc.go("home")

    def test_open_url_new_tab_in_front(self):
        result = pc.open_url("https://github.com")
        self.assertEqual(result["opened"], "https://github.com")
        self.assertEqual(self.live.page().url, "https://github.com")
        self.assertEqual(self.fronted, [500])
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            pc.open_url("file:///C:/secret.txt")

    def test_read_parts_and_find(self):
        self.page.text = "word " * 1000
        result = pc.read()
        self.assertEqual((result["part"], result["parts"]), (1, 4))
        self.assertIn("part=2", result["note"])
        self.assertEqual(pc.read(part=99)["part"], 4)
        self.page.text = "Welcome. Settings. Delete account is under Settings."
        found = pc.read(find="delete account")
        self.assertEqual(found["found"], 1)
        self.assertIn("Delete account", found["text"])
        self.assertIn("does not appear", pc.read(find="zzz")["note"])



MENU = """- button "Beállítások" [ref=e20] [cursor=pointer] [box=900,300,200,30]
- button "Súgó" [ref=e21] [cursor=pointer] [box=900,330,200,30]
"""


class TestStableNumbersAndChanges(Case):
    """Owner's YouTube test: the profile menu sat at the end of the page and was cut off."""

    def numbers(self, result):
        return {line.split('"')[1]: int(line.split(":")[0]) for line in result["elements"].splitlines()}

    def test_numbers_stay_the_same_across_lists(self):
        _, first = pc.list_page()
        self.page.snapshot = '- button "Inserted first" [ref=e30] [box=0,0,50,20]\n' + SNAP
        _, second = pc.list_page()
        before, after = self.numbers(first), self.numbers(second)
        for name in ("lofi hip hop radio", "Accept all"):
            self.assertEqual(before[name], after[name])
        self.assertEqual(after["Inserted first"], 5)  # a new element gets the next free number

    def test_after_a_click_only_what_appeared_is_shown(self):
        pc.list_page("open settings")
        pc.click(self.element("e4", "Fiók menüje"))
        self.page.snapshot = SNAP + MENU  # the menu is added at the END of the page
        everything, result = pc.list_page("open settings")
        self.assertEqual([line.split('"')[1] for line in result["elements"].splitlines()], ["Beállítások", "Súgó"])
        self.assertIn("Only what appeared after your click", result["note"])
        self.assertIn("e3", {el.ref for el in everything})  # older numbers stay usable
        _, full = pc.list_page("open settings")  # listing again shows the whole page
        self.assertEqual(full["count"], 6)
        # The menu the click opened stays first (on YouTube it sits at the end of the page).
        self.assertEqual([line.split('"')[1] for line in full["elements"].splitlines()[:2]], ["Beállítások", "Súgó"])
        pc.click(self.element("e9", "Accept all"))  # the next click ends that
        self.page.snapshot = SNAP + MENU
        _, after = pc.list_page()
        self.assertEqual(after["elements"].splitlines()[0].split('"')[1], "Search")

    def test_new_elements_come_first_even_without_a_click(self):
        pc.list_page()
        self.page.snapshot = SNAP + MENU
        _, result = pc.list_page("open settings")
        self.assertEqual(result["elements"].splitlines()[0].split('"')[1], "Beállítások")

    def test_nothing_new_after_a_click_gives_the_whole_page(self):
        pc.list_page()
        pc.click(self.element("e4"))
        _, result = pc.list_page()
        self.assertEqual(result["count"], 4)
        self.assertIn("Nothing new appeared after your click", result["note"])

    def test_another_page_starts_numbering_again(self):
        pc.list_page()
        self.page.url = "https://www.youtube.com/watch?v=1"
        self.page.snapshot = '- button "Like" [ref=e50] [box=0,0,50,20]\n'
        _, result = pc.list_page()
        self.assertEqual(result["elements"], '1: Button "Like"')

    def test_a_fragment_change_is_the_same_page(self):
        pc.list_page()
        self.page.url += "#inbox/123"
        pc.click(self.element("e4"))
        self.page.snapshot = SNAP + MENU
        _, result = pc.list_page()
        self.assertIn("Only what appeared", result["note"])

    def test_on_screen_before_scrolled_away(self):
        self.page.snapshot = (
            '- link "Far video" [ref=e1] [box=0,2000,100,20]\n'
            '- link "Above" [ref=e2] [box=0,-500,100,20]\n'
            '- button "Visible menu" [ref=e3] [box=0,100,100,20]\n'
        )
        _, result = pc.list_page()
        self.assertEqual(result["elements"].splitlines()[0], '3: Button "Visible menu"')


class TestOpenReuse(Case):
    def test_same_address_switches_to_the_open_tab(self):
        result = pc.open_url("https://youtube.com")  # the tab shows https://www.youtube.com/
        self.assertIn("already open", result["note"])
        self.assertEqual(len(self.context.pages), 1)

    def test_other_address_opens_a_new_tab(self):
        pc.open_url("https://www.youtube.com/feed/subscriptions")
        self.assertEqual(len(self.context.pages), 2)

    def test_same_address(self):
        self.assertTrue(pc.same_address("https://www.youtube.com/", "http://youtube.com"))
        self.assertTrue(pc.same_address("https://mail.google.com/mail/#inbox", "https://mail.google.com/mail"))
        self.assertFalse(pc.same_address("https://youtube.com/?q=1", "https://youtube.com/"))
        self.assertFalse(pc.same_address("about:blank", "about:blank"))

class TestTabs(Case):
    def test_tabs_with_the_agent_window(self):
        second = self.context.new_page()
        second.url, second._title = "https://github.com/", "GitHub"
        self.live.set_current(second)
        tabs = pc.tabs()
        self.assertEqual([(t.title, t.program, t.active) for t in tabs],
                         [("YouTube", "www.youtube.com", False), ("GitHub", "github.com", True)])
        self.assertEqual({t.hwnd for t in tabs}, {500})
        self.assertEqual(self.session.launches, [False])  # listing never starts the browser

    def test_not_running_means_no_tabs(self):
        self.session.live = None
        self.assertEqual(pc.tabs(), [])
        self.assertEqual(pc.tab_problem(), "")  # a closed browser is not a problem

    def test_one_unreadable_tab_does_not_hide_the_others(self):
        broken = self.context.new_page()
        broken.url = "https://slow.example/"
        broken.title = lambda: (_ for _ in ()).throw(PwTimeout("busy"))
        self.assertEqual([t.title for t in pc.tabs()], ["YouTube", "https://slow.example/"])

    def test_a_failure_is_reported_not_hidden(self):
        def broken(fn, *, launch=True, timeout=None):
            raise RuntimeError("Browser: Target closed")

        self.session.call = broken
        self.assertEqual(pc.tabs(), [])
        self.assertIn("Target closed", pc.tab_problem())

    def test_focus_and_close(self):
        (tab,) = pc.tabs()
        self.assertTrue(pc.focus_tab(tab)["in_front"])
        self.assertTrue(pc.close_tab(tab)["closed"])

    def test_close_keeps_a_tab_that_asks_to_leave(self):
        self.page.leave_prompt = True
        (tab,) = pc.tabs()
        result = pc.close_tab(tab)
        self.assertFalse(result["closed"])
        self.assertIn("Do not choose", result["note"])


if __name__ == "__main__":
    unittest.main()
