"""Tests for input/site_pages.py: plain snapshot strings, no browser."""
import unittest

from desktop_ai_agent.input.page_elements import parse_snapshot
from desktop_ai_agent.input.site_pages import (
    PageItem,
    content_patterns,
    clean_title,
    drop_repeated_rows,
    fact_for,
    facts_by_box,
    inside_app,
    is_content,
    is_content_url,
    marked_refs,
    page_items,
    page_key,
    same_site,
    shell_names,
    site_host,
    why_not_follow,
    with_facts,
)

BASE = "https://www.example.com/start"

SNAP = """- banner [ref=e1] [box=0,0,1000,60]:
  - button "Account menu" [ref=e2] [box=900,10,40,40]
- navigation "Guide" [ref=e3] [box=0,60,200,600]:
  - link "History" [ref=e4] [cursor=pointer] [box=0,90,200,30]:
    - /url: /feed/history
- generic [ref=e5] [box=0,0,10,10]:
  - link "Loose link" [ref=e6] [cursor=pointer] [box=0,0,10,10]:
    - /url: https://www.example.com/loose
- main [ref=e7] [box=200,60,800,600]:
  - heading "Settings" [level=1] [ref=e8] [box=200,60,300,40]
  - heading "Tiny print" [level=5] [ref=e9] [box=200,100,300,20]
  - checkbox "Restricted mode" [ref=e10] [box=200,130,20,20]
  - row "Mail from Bob" [ref=e11] [box=200,160,600,20]
  - generic [ref=e12] [cursor=pointer] [box=200,200,300,100]:
    - text: A very long video card title that goes on and on and on and on forever
  - generic [ref=e13] [cursor=pointer] [box=200,300,300,30]:
    - text: Short card
"""


class TestItems(unittest.TestCase):
    def setUp(self):
        self.items = page_items(parse_snapshot(SNAP), BASE)
        self.by_label = {it.label: it for it in self.items}

    def test_areas(self):
        self.assertEqual(self.by_label["Account menu"].area, "nav")
        self.assertEqual(self.by_label["History"].area, "nav")
        self.assertEqual(self.by_label["Loose link"].area, "other")
        self.assertEqual(self.by_label["Restricted mode"].area, "main")

    def test_links_get_absolute_addresses(self):
        self.assertEqual(self.by_label["History"].url, "https://www.example.com/feed/history")
        self.assertEqual(self.by_label["Account menu"].url, "")

    def test_headings_up_to_level_3(self):
        self.assertEqual(self.by_label["Settings"].kind, "Heading")
        self.assertNotIn("Tiny print", self.by_label)

    def test_rows_and_long_cards_left_out(self):
        self.assertNotIn("Mail from Bob", self.by_label)
        self.assertFalse(any(label.startswith("A very long") for label in self.by_label))
        self.assertIn("Short card", self.by_label)

    def test_page_order(self):
        labels = [it.label for it in self.items]
        self.assertLess(labels.index("Account menu"), labels.index("Settings"))


class TestAddresses(unittest.TestCase):
    def test_site_host(self):
        self.assertEqual(site_host("https://WWW.YouTube.com/x"), "youtube.com")
        self.assertTrue(same_site("http://youtube.com/a", "youtube.com"))
        self.assertFalse(same_site("https://m.youtube.com/a", "youtube.com"))

    def test_page_key(self):
        self.assertEqual(page_key("https://www.a.com/x/"), page_key("http://a.com/x"))
        self.assertEqual(page_key("https://a.com/x#top"), page_key("https://a.com/x"))
        self.assertNotEqual(page_key("https://a.com/mail/#settings/general"), page_key("https://a.com/mail/"))
        self.assertEqual(page_key("https://a.com/?b=2&a=1"), page_key("https://a.com/?a=1&b=2"))

    def test_content_addresses(self):
        for url in (
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/@somechannel",
            "https://youtube.com/shorts/abc123XYZ00",
            "https://github.com/a/b/commit/9fceb02d0ae598e95dc970b74767f19372d61af8",
            "https://example.com/results?search_query=cats",
            "https://example.com/news/2024",
        ):
            self.assertTrue(is_content_url(url), url)
        for url in (
            "https://youtube.com/account_privacy",
            "https://github.com/settings/profile",
            "https://myaccount.google.com/u/0/personal-info",
            "https://example.com/settings?tab=security",
        ):
            self.assertFalse(is_content_url(url), url)

    def test_links_never_followed(self):
        host = "example.com"
        self.assertEqual(why_not_follow("https://example.com/settings", host), "")
        self.assertEqual(why_not_follow("https://other.com/settings", host), "another site")
        self.assertEqual(why_not_follow("mailto:a@b.c", host), "not a web page")
        self.assertEqual(why_not_follow("https://example.com/logout", host), "could change something")
        self.assertEqual(why_not_follow("https://example.com/account/delete", host), "could change something")
        self.assertEqual(why_not_follow("https://example.com/u?action=unsubscribe", host), "could change something")
        self.assertEqual(why_not_follow("https://example.com/files/report.pdf", host), "a file")
        self.assertEqual(why_not_follow("https://example.com/watch?v=abc123XYZ", host), "content")

    def test_lists_of_same_pattern_links_are_content_only_in_main(self):
        repos = [PageItem(f"repo{i}", "Hyperlink", f"e{i}", f"https://github.com/me/repo{i}", "main") for i in range(6)]
        menu = [PageItem(f"m{i}", "Hyperlink", f"n{i}", f"https://github.com/features/f{i}", "nav") for i in range(6)]
        patterns = content_patterns(repos + menu)
        self.assertTrue(all(is_content(it, patterns) for it in repos))
        self.assertFalse(any(is_content(it, patterns) for it in menu))
        self.assertFalse(is_content(PageItem("Save", "Button", "x"), patterns))


class TestFactsAndGroups(unittest.TestCase):
    def test_items_keep_their_box_and_link_group(self):
        snap = "\n".join([
            "- navigation [ref=e1] [box=0,0,100,100]:",
            "  - list [ref=e2] [box=0,0,100,60]:",
            '    - link "A" [ref=e3] [box=0,0,10,10]:',
            "      - /url: /a",
            '    - link "B" [ref=e4] [box=0,20,10,10]:',
            "      - /url: /b",
            '    - link "C" [ref=e5] [box=0,40,10,10]:',
            "      - /url: /c",
            '  - link "D" [ref=e6] [box=0,80,10,10]:',
            "    - /url: /d",
        ])
        nodes = parse_snapshot(snap)
        items = {it.label: it for it in page_items(nodes, BASE)}
        self.assertEqual(items["A"].box, (0, 0, 10, 10))
        self.assertEqual(items["A"].group, items["C"].group)  # the list
        self.assertNotEqual(items["A"].group, items["D"].group)  # the navigation around it

    def test_facts_by_box_merge_and_near_match(self):
        facts = facts_by_box([
            [10, 20, 30, 40, "", True, False],
            [10, 20, 30, 40, "https://a.com/x", False, False],
            [0, 0, 5, 5, "javascript:void(0)", False, True],
            ["bad"],
        ])
        self.assertEqual(facts[(10, 20, 30, 40)].href, "https://a.com/x")
        self.assertTrue(facts[(10, 20, 30, 40)].popup)
        self.assertEqual(facts[(0, 0, 5, 5)].href, "")  # not a web address
        self.assertIs(fact_for((11, 20, 30, 41), facts), facts[(10, 20, 30, 40)])
        self.assertIsNone(fact_for((50, 50, 5, 5), facts))
        self.assertIsNone(fact_for(None, facts))

    def test_with_facts_fills_links_and_menus_but_not_form_controls(self):
        facts = facts_by_box([[0, 0, 10, 10, "https://a.com/help", True, False],
                              [0, 20, 10, 10, "https://a.com/card", False, False]])
        items = [PageItem("Help", "MenuItem", "e1", box=(0, 0, 10, 10)),
                 PageItem("Dark theme", "CheckBox", "e2", box=(0, 20, 10, 10))]
        done = with_facts(items, facts)
        self.assertEqual((done[0].url, done[0].popup), ("https://a.com/help", True))
        self.assertEqual(done[1].url, "")

    def test_tracking_and_language_parameters_do_not_make_another_page(self):
        self.assertEqual(page_key("https://a.com/x?utm_source=yt&utm_campaign=c"), page_key("https://a.com/x"))
        self.assertEqual(page_key("https://a.com/x?hl=hu&tab=2"), page_key("https://a.com/x?tab=2"))


class TestInsideTheApp(unittest.TestCase):
    def shell(self, *labels, area="nav"):
        return shell_names([PageItem(label, "Hyperlink", f"e{i}", area=area) for i, label in enumerate(labels)])

    def test_shell_is_header_and_sidebar_names_only(self):
        items = [PageItem("Home", "Hyperlink", "e1", area="nav"), PageItem("Video", "Hyperlink", "e2", area="main"),
                 PageItem("Title", "Heading", "e3", area="nav")]
        self.assertEqual(shell_names(items), frozenset({"home"}))

    def test_a_page_with_the_same_header_is_inside(self):
        app = self.shell("Home", "Shorts", "Subscriptions", "You", "History", "Account menu", "Search", "Create")
        self.assertTrue(inside_app(self.shell("Home", "Shorts", "Subscriptions", "You", "Account menu"), app))
        self.assertFalse(inside_app(self.shell("Legal home", "Policies", "Contact", "Home"), app))

    def test_tiny_start_pages_let_everything_in(self):
        self.assertTrue(inside_app(frozenset(), self.shell("Home", "About")))


class TestMarkedRefs(unittest.TestCase):
    def test_editable_and_focusable_by_box(self):
        nodes = parse_snapshot("\n".join([
            "- generic [ref=e1] [box=0,0,100,20]: Add a comment",
            '- generic "Write" [ref=e2] [box=0,30,100,20]',
            "- generic [ref=e3] [box=0,60,100,20]",
        ]))
        facts = facts_by_box([[0, 0, 100, 20, "", False, False, True], [0, 30, 100, 20, "", False, True, False],
                              [0, 60, 100, 20, "", False, False]])  # an old 7-field row still reads
        marks = marked_refs(nodes, facts)
        self.assertEqual((marks.editable, marks.focusable, marks.points), (frozenset({"e2"}), frozenset({"e1"}), {}))

    def test_an_inline_placeholder_marks_its_line_with_the_point_to_click(self):
        nodes = parse_snapshot('- generic [ref=e12] [box=8,110,1264,18]: Add a comment')
        marks = marked_refs(nodes, facts_by_box([[8, 110, 130, 17, "", False, False, True]]))
        self.assertEqual(marks.focusable, frozenset({"e12"}))
        self.assertEqual(marks.points, {"e12": (65, 8)})

    def test_a_huge_container_does_not_stand_for_a_tiny_element(self):
        nodes = parse_snapshot('- generic [ref=e1] [box=0,0,1280,900]: everything')
        self.assertEqual(marked_refs(nodes, facts_by_box([[10, 10, 5, 5, "", False, False, True]])).focusable, frozenset())


class TestFieldText(unittest.TestCase):
    def test_marks_carry_the_text_of_unnamed_fields_only(self):
        nodes = parse_snapshot("\n".join([
            "- textbox [ref=e1] [box=52,567,105,17]",
            '- textbox "Search" [ref=e2] [box=0,0,100,20]',
        ]))
        facts = facts_by_box([[52, 567, 105, 17, "", False, False, False, "Add a comment..."],
                              [0, 0, 100, 20, "", False, False, False, "Search"]])
        self.assertEqual(marked_refs(nodes, facts).names, {"e1": "Add a comment..."})


class TestRepeatedRowsAndTitles(unittest.TestCase):
    def test_a_feed_of_look_alike_buttons_becomes_one(self):
        rows = [PageItem(f"Subscribe to channel: Name {i}", "Button", f"e{i}", area="main") for i in range(5)]
        rows += [PageItem(f"Go to the channel: Name {i}", "Hyperlink", f"g{i}", area="main") for i in range(3)]
        keep = [PageItem("Settings", "Hyperlink", "s", area="nav"),
                PageItem("Subscribe to this site now", "Button", "x", area="main")]
        labels = [it.label for it in drop_repeated_rows(rows + keep)]
        self.assertEqual(sum(l.startswith("Subscribe to channel") for l in labels), 1)
        self.assertEqual(sum(l.startswith("Go to the channel") for l in labels), 3)  # under 4: kept
        self.assertIn("Settings", labels)
        self.assertIn("Subscribe to this site now", labels)

    def test_menus_are_left_alone(self):
        rows = [PageItem(f"Switch to account number {i}", "MenuItem", f"e{i}", area="menu") for i in range(6)]
        self.assertEqual(len(drop_repeated_rows(rows)), 6)

    def test_the_unread_count_is_not_part_of_a_title(self):
        self.assertEqual(clean_title("(2769) YouTube"), "YouTube")
        self.assertEqual(clean_title("(3) Inbox (5) - Mail"), "Inbox (5) - Mail")
        self.assertEqual(clean_title("YouTube"), "YouTube")
