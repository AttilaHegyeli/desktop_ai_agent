"""Tests for action/tools/site_crawler.py against a fake site in a fake browser:
no real browser, page or network is touched."""
import unittest
from unittest import mock

from desktop_ai_agent.action.tools import browser_session as bs
from desktop_ai_agent.action.tools import site_crawler as sc

PwError = type("Error", (Exception,), {"__module__": "playwright._impl._errors"})

ROOT = "https://example.com/"


def link(ref, name, url, indent="  "):
    return f'{indent}- link "{name}" [ref={ref}] [cursor=pointer] [box=0,0,100,20]:\n{indent}  - /url: {url}\n'


HOME = (
    "- banner [ref=e1] [box=0,0,1000,60]:\n"
    '  - button "Account menu" [ref=e2] [box=900,10,40,40]\n'
    '  - button "Like" [ref=e3] [box=800,10,40,40]\n'
    '  - button "Delete everything" [ref=e4] [box=700,10,40,40]\n'
    '  - button "Shop" [ref=e30] [box=600,10,40,40]\n'
    '- navigation "Guide" [ref=e5] [box=0,60,200,600]:\n'
    + link("e6", "Home", "/")
    + link("e7", "History", "/feed/history")
    + link("e8", "Log out", "/logout")
    + link("e9", "Other site", "https://other.com/x")
    + "- main [ref=e10] [box=200,60,800,600]:\n"
    '  - heading "Recommended" [level=1] [ref=e11] [box=200,60,300,40]\n'
    + link("e12", "Video one", "/watch?v=abc123XYZ")
    + link("e13", "About", "/about")
)
MENU = (
    '- menu [ref=e20] [box=850,60,150,100]:\n'
    '  - menuitem "Settings" [ref=e21] [cursor=pointer] [box=850,60,150,30]:\n'
    "    - /url: /account\n"
    '  - menuitem "Help" [ref=e22] [box=850,90,150,30]\n'
)
ACCOUNT = (
    "- main [ref=e40] [box=200,60,800,600]:\n"
    '  - heading "Settings" [level=1] [ref=e41] [box=200,60,300,40]\n'
    '  - checkbox "Restricted mode" [ref=e42] [box=200,130,20,20]\n'
    + link("e43", "Privacy", "/account_privacy")
)


class FakeSite:
    def __init__(self):
        self.pages = {
            ROOT: ("Home", HOME),
            ROOT + "feed/history": ("History", "- heading \"History\" [level=1] [ref=e50] [box=0,0,10,10]\n"),
            ROOT + "account": ("Settings", ACCOUNT),
            ROOT + "account_privacy": ("Privacy", "- heading \"Privacy\" [level=1] [ref=e60] [box=0,0,10,10]\n"),
            ROOT + "about": ("About", "- heading \"About us\" [level=1] [ref=e70] [box=0,0,10,10]\n"),
            ROOT + "shop": ("Shop", ""),
            ROOT + "help": ("Help", ""),
        }
        # Menu entries the snapshot shows without /url: their address comes from the page.
        self.hrefs = {"e22": ROOT + "help"}
        self.menus = {"e2": MENU}
        self.popups = {"e2", "e4", "e30"}
        self.navigates = {"e30": ROOT + "shop"}
        self.redirects = {}
        self.broken = set()
        self.visits, self.clicks, self.checked = [], [], []


class FakeKeyboard:
    def __init__(self, tab):
        self.tab = tab

    def press(self, key):
        if key == "Escape":
            self.tab.menu = None


class FakeLoc:
    def __init__(self, tab, ref):
        self.tab, self.ref = tab, ref

    def evaluate(self, js, arg=None, timeout=None):
        if js == sc.HREF_JS:
            return self.tab.site.hrefs.get(self.ref, "")
        assert js == sc.POPUP_JS
        self.tab.site.checked.append(self.ref)
        return self.ref in self.tab.site.popups

    def click(self, timeout=None):
        site = self.tab.site
        site.clicks.append(self.ref)
        if self.ref in site.navigates:
            self.tab.url = site.navigates[self.ref]
        else:
            self.tab.menu = self.ref


class FakeTab:
    def __init__(self, context, site, url="about:blank"):
        self.context, self.site, self.url = context, site, url
        self.menu = None
        self.closed = False
        self.fronted = 0
        self.keyboard = FakeKeyboard(self)

    def goto(self, url, wait_until=None, timeout=None):
        self.site.visits.append(url)
        url = self.site.redirects.get(url, url)
        if url in self.site.broken:
            raise PwError("net::ERR_ABORTED")
        self.url, self.menu = url, None

    def wait_for_load_state(self, state, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return self.site.pages.get(self.url, ("Elsewhere", ""))[0]

    def aria_snapshot(self, mode=None, boxes=None, timeout=None):
        text = self.site.pages.get(self.url, ("", ""))[1]
        return text + (self.site.menus.get(self.menu, "") if self.menu else "")

    def locator(self, selector):
        return FakeLoc(self, selector[len("aria-ref="):])

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def bring_to_front(self):
        self.fronted += 1


class FakeContext:
    def __init__(self, site):
        self.site = site
        self.pages = []

    def new_page(self):
        tab = FakeTab(self, self.site)
        self.pages.append(tab)
        return tab


class SyncSession:
    def __init__(self, live):
        self.live = live

    def call(self, fn, *, launch=True, timeout=None):
        return fn(self.live)


class Case(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.context = FakeContext(self.site)
        self.user_tab = FakeTab(self.context, self.site, ROOT)
        self.context.pages.append(self.user_tab)
        browser = mock.Mock()
        browser.is_connected.return_value = True
        self.live = bs.Live(None, browser, self.context, bs.BrowserInfo("opera.exe", "opera"), 42)
        self.live.set_current(self.user_tab)
        patcher = mock.patch.object(sc, "_session", lambda: SyncSession(self.live))
        patcher.start()
        self.addCleanup(patcher.stop)

    def crawl(self, **kw):
        return sc.crawl_site(**kw)


class TestExploring(Case):
    def test_menus_and_navigation_first_then_the_rest(self):
        result = self.crawl()
        self.assertEqual(
            [p.url for p in result.pages],
            [ROOT, ROOT + "feed/history", ROOT + "account", ROOT + "help", ROOT + "shop", ROOT + "about",
             ROOT + "account_privacy"],
        )
        self.assertEqual(result.host, "example.com")
        self.assertEqual(result.partial, "")

    def test_never_opens_risky_foreign_or_content_links(self):
        self.crawl()
        visited = " ".join(self.site.visits)
        for bad in ("logout", "other.com", "watch?v="):
            self.assertNotIn(bad, visited)

    def test_risky_buttons_are_never_checked_or_clicked(self):
        self.crawl()
        self.assertNotIn("e4", self.site.checked)
        self.assertNotIn("e4", self.site.clicks)
        self.assertNotIn("e3", self.site.clicks)  # no menu: not clicked

    def test_menu_items_keep_their_button(self):
        result = self.crawl()
        settings = next(it for it in result.items if it.label == "Settings" and it.kind == "MenuItem")
        self.assertEqual((settings.menu, settings.area, settings.page), ("Account menu", "menu", 0))
        self.assertEqual(settings.url, ROOT + "account")
        help_item = next(it for it in result.items if it.label == "Help")
        self.assertEqual((help_item.menu, help_item.url), ("Account menu", ROOT + "help"))  # read from the page

    def test_way_to_each_page(self):
        result = self.crawl()
        via = {p.url: p.via for p in result.pages}
        self.assertEqual(via[ROOT], "")
        self.assertEqual(via[ROOT + "account"], "Account menu › Settings")
        self.assertEqual(via[ROOT + "account_privacy"], "Account menu › Settings › Privacy")
        self.assertEqual(via[ROOT + "shop"], "Shop")  # a button that is really a link

    def test_settings_controls_and_headings_are_items(self):
        result = self.crawl()
        labels = {(it.label, it.kind) for it in result.items}
        self.assertIn(("Restricted mode", "CheckBox"), labels)
        self.assertIn(("About us", "Heading"), labels)
        self.assertNotIn(("Video one", "Hyperlink"), labels)

    def test_each_menu_button_is_opened_once_per_site(self):
        self.site.pages[ROOT + "about"] = ("About", HOME)  # the same header again
        self.crawl()
        self.assertEqual(self.site.clicks.count("e2"), 1)

    def test_mapping_tab_closes_and_the_user_tab_stays(self):
        self.crawl()
        tabs = [t for t in self.context.pages if not t.is_closed()]
        self.assertEqual(tabs, [self.user_tab])
        self.assertIs(self.live.page(), self.user_tab)
        self.assertEqual(self.user_tab.url, ROOT)
        self.assertGreater(self.user_tab.fronted, 0)


class TestLimits(Case):
    def test_page_limit(self):
        self.assertEqual(len(self.crawl(max_pages=2).pages), 2)

    def test_stop_keeps_what_was_mapped(self):
        calls = iter([False, False, True])
        result = self.crawl(should_stop=lambda: next(calls, True))
        self.assertEqual(result.partial, "stopped")
        self.assertGreaterEqual(len(result.pages), 1)

    def test_time_limit(self):
        ticks = iter(range(0, 1000, 100))
        result = self.crawl(max_seconds=250, clock=lambda: float(next(ticks)))
        self.assertEqual(result.partial, "time limit")

    def test_redirect_to_another_site_and_broken_pages_are_skipped(self):
        self.site.redirects[ROOT + "about"] = "https://accounts.other.com/login"
        self.site.broken.add(ROOT + "feed/history")
        result = self.crawl()
        urls = [p.url for p in result.pages]
        self.assertNotIn(ROOT + "about", urls)
        self.assertNotIn(ROOT + "feed/history", urls)
        self.assertEqual(result.failed, 2)

    def test_not_a_web_page(self):
        self.user_tab.url = "about:blank"
        with self.assertRaisesRegex(LookupError, "not a web site"):
            self.crawl()
        self.assertEqual([t for t in self.context.pages if not t.is_closed()], [self.user_tab])

    def test_no_page_open(self):
        self.user_tab.closed = True
        self.live.current = None
        with self.assertRaisesRegex(LookupError, "web_open"):
            self.crawl()
