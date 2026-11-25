"""Tests for action/tools/site_map.py, page_control.show_item and the site_map handler.
The crawl, the embeddings and the browser are fakes; maps go to a temporary folder
(conftest). Nothing real is started, reached or written."""
import json
import unittest
from unittest import mock

from desktop_ai_agent.action import handlers
from desktop_ai_agent.action.tools import browser_session as bs
from desktop_ai_agent.action.tools import page_control as pc
from desktop_ai_agent.action.tools import site_map as sm
from desktop_ai_agent.action.tools import ui_control
from desktop_ai_agent.action.tools.site_crawler import CrawledItem, CrawledPage, CrawlResult
from desktop_ai_agent.core import embeddings
from desktop_ai_agent.input.page_elements import PageElement

# Fake meaning: each label points one way; "settings" and "Beállítások" point the same way.
DIRECTIONS = {
    "settings": [1, 0, 0, 0], "beállítások": [1, 0, 0, 0],
    "history": [0, 1, 0, 0], "előzmények": [0, 1, 0, 0],
    "restricted mode": [0, 0, 1, 0], "korlátozott mód": [0, 0, 1, 0],
    "help": [0.3, 0.3, 0.3, 0], "súgó": [0.3, 0.3, 0.3, 0],
    "zebra crossing": [0.3, 0, 0, 1],  # a little like "settings": a loose match
}
UNRELATED = [0, 0, 0, 1]


def fake_embed(texts, model=None, should_stop=None):
    fake_embed.calls.append(list(texts))
    return [embeddings.normalize(DIRECTIONS.get(t.casefold(), UNRELATED)) for t in texts]


fake_embed.calls = []


def crawl_result(partial=""):
    pages = [
        CrawledPage("https://www.youtube.com/", "YouTube"),
        CrawledPage("https://www.youtube.com/account", "Beállítások - YouTube", "Fiók menüje › Beállítások"),
    ]
    items = [
        CrawledItem("Előzmények", "Hyperlink", 0, "nav", url="https://www.youtube.com/feed/history"),
        CrawledItem("Beállítások", "MenuItem", 0, "menu", "Fiók menüje", "https://www.youtube.com/account"),
        CrawledItem("Súgó", "MenuItem", 0, "menu", "Fiók menüje"),
        CrawledItem("Beállítások", "Heading", 1, "main"),
        CrawledItem("Korlátozott mód", "CheckBox", 1, "main"),
    ]
    return CrawlResult("youtube.com", "https://www.youtube.com/", pages, items, partial)


class Case(unittest.TestCase):
    def setUp(self):
        fake_embed.calls = []
        self.shown_calls = []
        self.show_answer = (PageElement(57, "Beállítások", "MenuItem", "menuitem", "e9"), {"page": "YouTube", "site": "www.youtube.com", "shown": True, "element": 57})
        self.url = "https://www.youtube.com/"
        self.crawled = crawl_result()
        self.crawl_stops = []
        for name, value in {
            "_embed": fake_embed,
            "_crawl": lambda should_stop: self.crawl_stops.append(should_stop) or self.crawled,
            "_current_page": lambda hwnd: (self.url, "YouTube", ""),
            "_show_item": lambda *args: self.shown_calls.append(args) or self.show_answer,
            "_now_text": lambda: "2026-11-25 10:00",
        }.items():
            patcher = mock.patch.object(sm, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pc.reset()
        self.addCleanup(pc.reset)

    def make_map(self, **crawl):
        site, note = sm.build_map(crawl_result(**crawl))
        sm.save_map(site)
        return site


class TestStorage(Case):
    def test_save_and_load(self):
        site = self.make_map()
        back = sm.load_map("youtube.com")
        self.assertEqual(back.items, site.items)
        self.assertEqual(back.pages, site.pages)
        self.assertEqual(back.model, embeddings.DEFAULT_MODEL)
        self.assertAlmostEqual(embeddings.dot(back.vectors[1], site.vectors[1]), 1.0, places=5)

    def test_missing_old_or_broken_maps(self):
        self.assertIsNone(sm.load_map("nothing.com"))
        self.make_map()
        path = sm.map_path("youtube.com")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(sm.load_map("youtube.com"))
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(sm.load_map("youtube.com"))

    def test_file_name_is_safe(self):
        self.assertEqual(sm.map_path("a.b/../c:d").name, "a.b_.._c_d.json")

    def test_same_texts_embedded_once(self):
        self.make_map()
        sent = fake_embed.calls[0]
        self.assertEqual(sent.count("Beállítások"), 1)  # twice in the map
        self.assertEqual(len(sent), 4 + 3)  # 4 names + 3 places
        self.assertIn("YouTube › Fiók menüje", sent)
        self.assertIn("Fiók menüje › Beállítások", sent)

    def test_places_are_saved_too(self):
        site = self.make_map()
        back = sm.load_map("youtube.com")
        self.assertEqual(set(back.context_vectors), set(site.context_vectors))

    def test_without_embeddings_the_map_still_works(self):
        with mock.patch.object(sm, "_embed", side_effect=embeddings.EmbeddingError("model missing")):
            site, note = sm.build_map(crawl_result())
        self.assertIsNone(site.vectors)
        self.assertEqual(note, "model missing")
        sm.save_map(site)
        self.assertIsNone(sm.load_map("youtube.com").vectors)


class TestRanking(Case):
    def labels(self, site, query, vector):
        return [site.items[h.index].label for h in sm.rank(site, query, vector)]

    def test_meaning_finds_the_hungarian_name(self):
        site = self.make_map()
        self.assertEqual(self.labels(site, "settings", fake_embed(["settings"])[0])[0], "Beállítások")
        self.assertEqual(self.labels(site, "restricted mode", fake_embed(["restricted mode"])[0])[0], "Korlátozott mód")

    def test_words_alone_without_meaning(self):
        site = self.make_map()
        self.assertEqual(self.labels(site, "korlatozott", None), ["Korlátozott mód"])
        self.assertEqual(self.labels(site, "zebra", None), [])

    def test_one_line_per_name_first_place_wins(self):
        site = self.make_map()
        hits = sm.rank(site, "settings", fake_embed(["settings"])[0])
        self.assertEqual(sum(site.items[h.index].label == "Beállítások" for h in hits), 1)
        self.assertEqual(site.items[hits[0].index].kind, "MenuItem")
        self.assertLessEqual(len(hits), sm.TOP_HITS_LOCAL)

    def test_the_right_place_wins_between_equal_names(self):
        pages = [CrawledPage("https://x.com/", "Home"), CrawledPage("https://x.com/s", "Settings", "Settings")]
        items = [CrawledItem("Értesítések", "Button", 0), CrawledItem("Értesítések", "Hyperlink", 1)]
        same = embeddings.normalize([1, 0, 0])
        site = sm.SiteMap("x.com", "https://x.com/", "now", pages, items, vectors=[same, same],
                          context_vectors={"Home": embeddings.normalize([0, 0, 1]),
                                           "Settings": embeddings.normalize([0, 1, 0])})
        query = embeddings.normalize([1, 1, 0])  # "notification settings"
        hits = sm.rank(site, "notification settings", query)
        self.assertEqual(site.items[hits[0].index].kind, "Hyperlink")
        self.assertEqual(len(hits), 1)  # one line per name

    def test_menu_and_navigation_entries_get_a_small_bonus(self):
        pages = [CrawledPage("https://x.com/", "Home")]
        items = [CrawledItem("Alpha", "Button", 0, "main"), CrawledItem("Beta", "Hyperlink", 0, "nav")]
        same = embeddings.normalize([1, 0, 0])
        site = sm.SiteMap("x.com", "https://x.com/", "now", pages, items, vectors=[same, same])
        hits = sm.rank(site, "gamma", embeddings.normalize([1, 0, 0]))
        self.assertEqual([site.items[h.index].label for h in hits], ["Beta", "Alpha"])

    def test_exact_name_first(self):
        site = self.make_map()
        self.assertEqual(self.labels(site, "Súgó", fake_embed(["settings"])[0])[0], "Súgó")

    def test_hit_line(self):
        site = self.make_map()
        self.assertEqual(sm.hit_line(1, site, 1), '1: MenuItem "Beállítások" in menu "Fiók menüje" on page "YouTube"')
        self.assertEqual(
            sm.hit_line(2, site, 4),
            '2: CheckBox "Korlátozott mód" on page "Beállítások - YouTube" (page reached by: Fiók menüje › Beállítások)',
        )


class TestOutline(Case):
    def test_outline_parts(self):
        text = sm.outline(self.make_map())
        self.assertIn("Site youtube.com", text)
        self.assertIn("Main sections: Előzmények", text)
        self.assertIn('Menu "Fiók menüje": Beállítások, Súgó', text)
        self.assertIn('"Beállítások - YouTube" (Fiók menüje › Beállítások)', text)

    def test_size_follows_the_model(self):
        crawl = crawl_result()
        crawl.pages += [CrawledPage(f"https://www.youtube.com/p{i}", f"Page number {i}", f"Way {i}") for i in range(300)]
        site, _ = sm.build_map(crawl)
        local, cloud = sm.outline(site, cloud=False), sm.outline(site, cloud=True)
        self.assertLessEqual(len(local), sm.OUTLINE_CHARS_LOCAL + 100)
        self.assertGreater(len(cloud), len(local))
        self.assertIn("more pages", local)


class TestMapAction(Case):
    def test_only_when_the_user_asks(self):
        with self.assertRaisesRegex(PermissionError, "map this site"):
            sm.run("map", request="where are the settings")
        self.assertEqual(self.crawl_stops, [])

    def test_plain_map_ends_with_the_fixed_reply(self):
        result, shown = sm.run("map", request="map this site")
        self.assertIsNone(shown)
        self.assertEqual(result["final_reply"], "The site is mapped (2 pages). I can introduce it to you or find sections you want.")
        self.assertEqual((result["pages"], result["items"]), (2, 5))
        self.assertIsNotNone(sm.load_map("youtube.com"))
        self.assertEqual(self.shown_calls, [])

    def test_partial_map_after_stop(self):
        self.crawled = crawl_result(partial="stopped")
        result, _ = sm.run("map", request="map the site")
        self.assertTrue(result["final_reply"].startswith("I mapped part of the site (2 pages) before it was stopped."))
        self.assertEqual(sm.load_map("youtube.com").partial, "stopped")

    def test_stop_reaches_the_crawl(self):
        sm.run("map", request="map it")
        should_stop = self.crawl_stops[0]
        self.assertFalse(should_stop())
        sm.cancel()
        self.assertTrue(should_stop())
        sm.allow()
        self.assertFalse(should_stop())

    def test_missing_embedding_model_is_said_in_the_reply(self):
        with mock.patch.object(sm, "_embed", side_effect=embeddings.EmbeddingError("run: ollama pull x")):
            result, _ = sm.run("map", request="map this site")
        self.assertIn("ollama pull x", result["final_reply"])

    def test_map_and_find_in_one_call(self):
        result, shown = sm.run("map", find="settings", request="map the site, where are the settings")
        self.assertNotIn("final_reply", result)
        self.assertEqual(shown.id, 57)
        self.assertIn('1: MenuItem "Beállítások"', result["matches"])
        self.assertEqual(self.shown_calls[0], ("https://www.youtube.com/", "Beállítások", "MenuItem", "Fiók menüje"))

    def test_nothing_mapped(self):
        self.crawled = CrawlResult("youtube.com", self.url, failed=3)
        with self.assertRaisesRegex(RuntimeError, "log in"):
            sm.run("map", request="map this site")

    def test_private_site_with_a_cloud_model_maps_but_asks_before_matches(self):
        self.crawled = CrawlResult(
            "mail.google.com", "https://mail.google.com/mail/u/0/",
            [CrawledPage("https://mail.google.com/mail/u/0/", "Inbox")], [CrawledItem("Settings", "Button", 0)],
        )
        result, shown = sm.run("map", find="settings", cloud=True, request="map this site and find settings")
        self.assertIsNone(shown)
        self.assertIn("Ask the user whether to proceed", result["note"])
        self.assertNotIn("matches", result)
        self.assertEqual(self.shown_calls, [])


class TestFindShowOutline(Case):
    def setUp(self):
        super().setUp()
        self.make_map()

    def test_find_shows_match_one(self):
        result, shown = sm.run("find", find="settings")
        self.assertEqual(shown.id, 57)
        self.assertIn("element 57", result["note"])
        self.assertIn("its number (2)", result["note"])
        self.assertEqual(result["matches"].splitlines()[0], '1: MenuItem "Beállítások" in menu "Fiók menüje" on page "YouTube"')

    def test_find_on_a_site_without_a_map(self):
        self.url = "https://github.com/"
        with self.assertRaisesRegex(LookupError, "github.com has no site map yet.*map this site"):
            sm.run("find", find="settings")

    def test_find_needs_words(self):
        with self.assertRaisesRegex(ValueError, "find needs"):
            sm.run("find", find="  ")

    def test_match_not_on_the_page_anymore(self):
        self.show_answer = (None, {"shown": False})
        result, shown = sm.run("find", find="settings")
        self.assertIsNone(shown)
        self.assertIn("was not found on the page now", result["note"])

    def test_browser_error_while_showing(self):
        with mock.patch.object(sm, "_show_item", side_effect=RuntimeError("Browser: gone")):
            result, shown = sm.run("find", find="settings")
        self.assertIsNone(shown)
        self.assertEqual(result["show_error"], "Browser: gone")

    def test_loose_matches_are_marked(self):
        result, _ = sm.run("find", find="zebra crossing")
        self.assertIn("loose matches", result["note"])

    def test_show_another_match(self):
        sm.run("find", find="settings")
        result, shown = sm.run("show", number=2)
        self.assertTrue(result["match"].startswith("2: "))
        self.assertEqual(len(self.shown_calls), 2)

    def test_show_needs_a_find_and_a_valid_number(self):
        with self.assertRaisesRegex(LookupError, "action find first"):
            sm.run("show", number=1)
        sm.run("find", find="settings")
        with self.assertRaisesRegex(ValueError, "between 1 and 2"):  # 2 matches were found
            sm.run("show", number=3)

    def test_new_chat_forgets_the_last_find(self):
        sm.run("find", find="settings")
        sm.reset()
        with self.assertRaises(LookupError):
            sm.run("show", number=1)

    def test_outline(self):
        result, shown = sm.run("outline")
        self.assertIsNone(shown)
        self.assertIn("Main sections", result["outline"])

    def test_meaning_off_when_the_model_is_gone(self):
        with mock.patch.object(sm, "_embed", side_effect=embeddings.EmbeddingError("not reachable")):
            result, _ = sm.run("find", find="korlatozott")
        self.assertIn("only words were compared", result["note"])
        self.assertIn("Korlátozott mód", result["matches"])

    def test_unknown_action(self):
        with self.assertRaises(ValueError):
            sm.run("crawl")


class TestCloudPrivateSites(Case):
    def setUp(self):
        super().setUp()
        self.url = "https://mail.google.com/mail/u/0/"
        site = sm.SiteMap("mail.google.com", self.url, "2026-11-25", [CrawledPage(self.url, "Inbox")],
                          [CrawledItem("Settings", "Button", 0)])
        sm.save_map(site)

    def test_find_and_outline_wait_for_the_users_ok(self):
        with self.assertRaises(PermissionError):
            sm.run("outline", cloud=True, request="introduce gmail")
        result, _ = sm.run("outline", cloud=True, request="yes proceed")
        self.assertIn("Site mail.google.com", result["outline"])

    def test_local_model_needs_no_ok(self):
        result, _ = sm.run("find", find="settings", cloud=False)
        self.assertIn("Settings", result["matches"])


class TestHandler(Case):
    def setUp(self):
        super().setUp()
        self.make_map()
        ui_control.reset()
        self.addCleanup(ui_control.reset)
        patcher = mock.patch.object(ui_control, "user_browser_in_front", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_shown_match_can_be_clicked_by_its_number(self):
        handlers.site_map({"action": "find", "find": "settings"})
        self.assertIn(57, ui_control._last.elements)

    def test_an_unshown_match_clears_the_old_list(self):
        ui_control.remember_page_elements([PageElement(3, "Old", "Button", "button", "e1")])
        self.show_answer = (None, {"shown": False})
        handlers.site_map({"action": "find", "find": "settings"})
        self.assertIsNone(ui_control._last)

    def test_a_plain_map_keeps_the_list(self):
        ui_control.remember_page_elements([PageElement(3, "Old", "Button", "button", "e1")])
        result = handlers.site_map({"action": "map", "request": "map this site"})
        self.assertIn("final_reply", result)
        self.assertIn(3, ui_control._last.elements)


# ---- page_control.show_item -----------------------------------------------------------

PAGE = """- banner [ref=e1] [box=0,0,1000,60]:
  - button "Fiók menüje" [ref=e2] [box=900,10,40,40]
- main [ref=e3] [box=0,60,1000,600]:
  - heading "Beállítások" [level=1] [ref=e4] [box=0,60,300,40]
  - checkbox "Korlátozott mód" [ref=e5] [box=0,130,20,20]
"""
MENU = """- menu [ref=e6] [box=850,60,150,100]:
  - menuitem "Beállítások" [ref=e7] [cursor=pointer] [box=850,60,150,30]
"""


class FakeLoc:
    def __init__(self, page, ref):
        self.page, self.ref = page, ref

    def click(self, timeout=None):
        self.page.log.append(("click", self.ref))
        self.page.menu_open = True

    def evaluate(self, js, arg=None):
        if js == pc.RISK_JS:
            return self.page.risky.get(self.ref, "")
        self.page.log.append(("outline", self.ref, arg))


class FakePage:
    def __init__(self, url):
        self.url, self.menu_open, self.log = url, False, []
        self.risky = {}

    def is_closed(self):
        return False

    def goto(self, url, wait_until=None, timeout=None):
        self.log.append(("goto", url))
        self.url, self.menu_open = url, False

    def wait_for_load_state(self, state, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def aria_snapshot(self, mode=None, boxes=None, timeout=None):
        return PAGE + (MENU if self.menu_open else "")

    def evaluate(self, js):
        return 800

    def title(self):
        return "Beállítások - YouTube"

    def locator(self, selector):
        return FakeLoc(self, selector[len("aria-ref="):])

    def bring_to_front(self):
        self.log.append(("front",))


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class TestShowItem(unittest.TestCase):
    def setUp(self):
        self.page = FakePage("https://www.youtube.com/")
        browser = mock.Mock()
        browser.is_connected.return_value = True
        live = bs.Live(None, browser, FakeContext(self.page), bs.BrowserInfo("opera.exe", "opera"), 42)

        class Session:
            def call(self, fn, *, launch=True, timeout=None):
                return fn(live)

            def known_pid(self):
                return 42, "opera"

        patcher = mock.patch.object(pc, "_session", return_value=Session())
        patcher.start()
        self.addCleanup(patcher.stop)
        pc.reset()
        self.addCleanup(pc.reset)

    def test_opens_the_menu_and_numbers_the_item(self):
        el, result = pc.show_item("https://youtube.com", "Beállítások", "MenuItem", "Fiók menüje")
        self.assertNotIn(("goto", "https://youtube.com"), self.page.log)  # same page: no reload
        self.assertIn(("click", "e2"), self.page.log)
        self.assertIn(("outline", "e7", pc.SHOW_MS), self.page.log)
        self.assertEqual((el.name, el.type, result["element"]), ("Beállítások", "MenuItem", el.id))

    def test_an_open_menu_is_not_clicked_again(self):
        self.page.menu_open = True  # a click would close it
        el, _ = pc.show_item("https://www.youtube.com/", "Beállítások", "MenuItem", "Fiók menüje")
        self.assertNotIn(("click", "e2"), self.page.log)
        self.assertEqual(el.type, "MenuItem")

    def test_goes_to_the_page_and_outlines_a_control(self):
        el, result = pc.show_item("https://www.youtube.com/account", "Korlátozott mód", "CheckBox")
        self.assertIn(("goto", "https://www.youtube.com/account"), self.page.log)
        self.assertEqual(el.type, "CheckBox")
        self.assertTrue(result["shown"])

    def test_a_heading_is_outlined_without_a_number(self):
        el, result = pc.show_item("https://www.youtube.com/", "Beállítások", "Heading")
        self.assertIsNone(el)
        self.assertTrue(result["shown"])
        self.assertIn(("outline", "e4", pc.SHOW_MS), self.page.log)

    def test_cut_names_still_match(self):
        el, _ = pc.show_item("https://www.youtube.com/", "Korlátozott m...", "CheckBox")
        self.assertEqual(el.name, "Korlátozott mód")

    def test_not_on_the_page(self):
        el, result = pc.show_item("https://www.youtube.com/", "Nope", "Button", "Missing menu")
        self.assertIsNone(el)
        self.assertFalse(result["shown"])
        self.assertEqual(result["menu_not_found"], "Missing menu")


class TestHowManyMatches(Case):
    def setUp(self):
        super().setUp()
        pages = [CrawledPage("https://www.youtube.com/", "YouTube")]
        items = [CrawledItem(f"Section {i}", "Hyperlink", 0, "nav", url=f"https://www.youtube.com/s{i}") for i in range(10)]
        site, _ = sm.build_map(CrawlResult("youtube.com", "https://www.youtube.com/", pages, items))
        sm.save_map(site)

    def lines(self, cloud):
        result, _ = sm.run("find", find="section", cloud=cloud)
        return result["matches"].splitlines()

    def test_a_local_model_gets_five_and_a_cloud_model_eight(self):
        self.assertEqual(len(self.lines(cloud=False)), 5)
        self.assertEqual(len(self.lines(cloud=True)), 8)

    def test_any_returned_match_can_be_shown(self):
        sm.run("find", find="section", cloud=True)
        result, _ = sm.run("show", number=8, cloud=True)
        self.assertTrue(result["match"].startswith("8: "))
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            sm.run("show", number=9, cloud=True)

    def test_the_note_asks_the_model_to_pick_by_its_own_reading(self):
        result, _ = sm.run("find", find="section", cloud=True)
        self.assertIn("(2 to 8)", result["note"])
        self.assertIn("site's language", result["note"])

    def test_the_tool_accepts_up_to_eight(self):
        from desktop_ai_agent.action.registry import build_default_registry

        spec = build_default_registry().get("site_map")
        self.assertEqual(spec.validate({"action": "show", "number": 8})["number"], 8)
