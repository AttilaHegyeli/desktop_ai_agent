"""Tests for input/site_pages.py: plain snapshot strings, no browser."""
import unittest

from desktop_ai_agent.input.page_elements import parse_snapshot
from desktop_ai_agent.input.site_pages import (
    PageItem,
    content_patterns,
    is_content,
    is_content_url,
    page_items,
    page_key,
    same_site,
    site_host,
    why_not_follow,
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
