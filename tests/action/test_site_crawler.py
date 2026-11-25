"""Tests for action/tools/site_crawler.py against a fake site in a fake browser:
no real browser, page or network is touched."""
import itertools
import unittest
from unittest import mock

from desktop_ai_agent.action.tools import browser_session as bs
from desktop_ai_agent.action.tools import site_crawler as sc
from desktop_ai_agent.input.page_elements import parse_snapshot
from desktop_ai_agent.input.site_pages import ELEMENT_FACTS_JS

PwError = type("Error", (Exception,), {"__module__": "playwright._impl._errors"})

ROOT = "https://example.com/"
_y = itertools.count(0, 25)  # every element gets its own box, as on a real page


def el(role, name, ref, url=None, indent="  "):
    line = f'{indent}- {role} "{name}" [ref={ref}] [cursor=pointer] [box=0,{next(_y)},100,20]'
    return line + (f":\n{indent}  - /url: {url}\n" if url else "\n")


def block(role, ref, body, name=""):
    label = f' "{name}"' if name else ""
    return f"- {role}{label} [ref={ref}] [box=0,{next(_y)},1000,600]:\n" + body


# The app's header and sidebar, the same on every page of the app.
HEADER = block("banner", "e1", (
    el("button", "Account menu", "e2") + el("button", "Like", "e3")
    + el("button", "Delete everything", "e4") + el("button", "Shop", "e30")
)) + block("navigation", "e5", (
    el("link", "Home", "e6", "/") + el("link", "History", "e7", "/feed/history")
    + el("link", "Log out", "e8", "/logout") + el("link", "Other site", "e9", "https://other.com/x")
    + "  - list [ref=e90] [box=0,9000,300,100]:\n"
    + el("link", "Terms", "e91", "/t/terms", "    ") + el("link", "Press", "e92", "/about/press", "    ")
    + el("link", "Creators", "e93", "/creators", "    ") + el("link", "Ads", "e94", "/ads", "    ")
), "Guide")
HOME = HEADER + block("main", "e10", (
    '  - heading "Recommended" [level=1] [ref=e11] [box=200,60,300,40]\n'
    + el("link", "Video one", "e12", "/watch?v=abc123XYZ") + el("link", "About", "e13", "/about")
))
MENU = block("menu", "e20", (
    el("menuitem", "Settings", "e21", "/account")
    + el("menuitem", "Help", "e22")  # no /url in the snapshot: its address comes from the page
))
ACCOUNT = HEADER + block("main", "e40", (
    '  - heading "Settings" [level=1] [ref=e41] [box=200,60,300,40]\n'
    '  - checkbox "Restricted mode" [ref=e42] [box=200,130,20,20]\n'
    + el("link", "Privacy", "e43", "/account_privacy")
))
# A legal page: a header of its own, nothing in common with the app's.
LEGAL = block("banner", "e70", (
    el("link", "Legal home", "e71", "/t/") + el("link", "Policies", "e72", "/t/policies")
    + el("link", "Contact", "e73", "/t/contact") + el("button", "Language", "e74")
)) + '- heading "Terms of service" [level=1] [ref=e75] [box=0,700,300,40]\n'


def page(heading):
    return HEADER + f'- heading "{heading}" [level=1] [ref=e60] [box=0,800,10,10]\n'


class FakeSite:
    def __init__(self):
        self.pages = {
            ROOT: ("Home", HOME),
            ROOT + "feed/history": ("History", page("History")),
            ROOT + "account": ("Settings", ACCOUNT),
            ROOT + "account_privacy": ("Privacy", page("Privacy")),
            ROOT + "about": ("About", page("About us")),
            ROOT + "shop": ("Shop", page("Shop")),
            ROOT + "help": ("Help", page("Help")),
            ROOT + "t/terms": ("Terms", LEGAL),
            ROOT + "about/press": ("Press", LEGAL),
            ROOT + "creators": ("Creators", LEGAL),
            ROOT + "ads": ("Ads", LEGAL),
        }
        self.menus = {"e2": MENU}
        self.popups = {"e2", "e4", "e30"}
        self.navigates = {"e30": ROOT + "shop"}
        self.hrefs = {"e22": ROOT + "help"}
        self.risky_code = {}  # ref -> what its page code says
        self.redirects = {}
        self.broken = set()
        self.visits, self.clicks = [], []
        self.fact_calls = 0


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
        assert js == sc.code_risk.__globals__["RISK_JS"]
        return self.tab.site.risky_code.get(self.ref, "")

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

    def evaluate(self, js):
        assert js == ELEMENT_FACTS_JS  # the only script the crawler runs
        self.site.fact_calls += 1
        rows = []
        for node in parse_snapshot(self.aria_snapshot()):
            if node.ref and node.box:
                x, y, w, h = (int(v) for v in node.box)
                rows.append([x, y, w, h, self.site.hrefs.get(node.ref, ""), node.ref in self.site.popups, False])
        return rows

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
    def test_nearest_first_menus_first_then_navigation_then_the_rest(self):
        result = self.crawl()
        self.assertEqual(
            [p.url for p in result.pages],
            [ROOT, ROOT + "account", ROOT + "help", ROOT + "shop", ROOT + "feed/history", ROOT + "about",
             ROOT + "account_privacy"],
        )
        self.assertEqual(result.host, "example.com")
        self.assertEqual(result.partial, "")

    def test_legal_pages_are_left_out_and_the_rest_of_their_group_skipped(self):
        result = self.crawl()
        urls = " ".join(p.url for p in result.pages)
        self.assertNotIn("/t/terms", urls)
        self.assertEqual((result.outside, result.skipped), (2, 2))  # terms + press visited, 2 skipped
        self.assertNotIn(ROOT + "creators", self.site.visits)
        self.assertNotIn(ROOT + "ads", self.site.visits)
        self.assertFalse(any(it.label == "Terms of service" for it in result.items))

    def test_never_opens_risky_foreign_or_content_links(self):
        self.crawl()
        visited = " ".join(self.site.visits)
        for bad in ("logout", "other.com", "watch?v="):
            self.assertNotIn(bad, visited)

    def test_risky_and_plain_buttons_are_never_clicked(self):
        self.crawl()
        self.assertNotIn("e4", self.site.clicks)  # "Delete everything" opens a menu but is risky
        self.assertNotIn("e3", self.site.clicks)  # "Like" opens nothing

    def test_one_script_call_per_page_read(self):
        result = self.crawl()
        menus_opened = 1  # the account menu (the shop button navigated away)
        loaded = len(result.pages) + result.outside
        self.assertEqual(self.site.fact_calls, loaded + menus_opened)

    def test_menu_items_keep_their_button_and_address(self):
        result = self.crawl()
        settings = next(it for it in result.items if it.label == "Settings" and it.kind == "MenuItem")
        self.assertEqual((settings.menu, settings.area, settings.page), ("Account menu", "menu", 0))
        self.assertEqual(settings.url, ROOT + "account")
        help_item = next(it for it in result.items if it.label == "Help" and it.kind == "MenuItem")
        self.assertEqual((help_item.menu, help_item.url), ("Account menu", ROOT + "help"))

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
        self.crawl()  # every page of the app has the account menu button
        self.assertEqual(self.site.clicks.count("e2"), 1)

    def test_time_per_page_is_kept(self):
        ticks = itertools.count(0, 2)
        result = self.crawl(clock=lambda: float(next(ticks)))
        self.assertTrue(all(p.seconds > 0 for p in result.pages))

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
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.outside, 3)  # the login page + 2 legal pages

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


class TestRiskyCode(Case):
    def test_a_menu_button_whose_code_is_risky_is_not_clicked(self):
        self.site.risky_code["e2"] = "delete"
        result = self.crawl()
        self.assertNotIn("e2", self.site.clicks)
        self.assertFalse(any(it.menu == "Account menu" for it in result.items))
