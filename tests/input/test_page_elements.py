"""Tests for input/page_elements.py: snapshot text in, numbered page elements out."""
import unittest

from desktop_ai_agent.input import page_elements as pe

# Shaped like Playwright's page.aria_snapshot(mode="ai", boxes=True).
SNAP = """- banner [ref=e1]:
  - link "YouTube Home" [ref=e2] [cursor=pointer] [box=10,10,90,20]:
    - /url: /
  - combobox "Search" [ref=e3] [box=300,10,400,30]
  - button "Search" [ref=e4] [cursor=pointer] [box=700,10,40,30]
- main [ref=e5]:
  - link "lofi hip hop radio" [ref=e6] [cursor=pointer] [box=10,900,300,40]
  - generic [ref=e7] [cursor=pointer] [box=10,300,200,50]:
    - generic [ref=e8] [cursor=pointer] [box=10,300,200,20]:
      - text: Card title
  - button "Accept all" [ref=e9] [box=10,500,100,30]
  - textbox [ref=e10] [box=10,600,100,30]:
    - /placeholder: Add a comment
  - button "Hidden" [ref=e11] [box=0,0,0,0]
  - link "Home" [ref=e12] [box=0,-200,10,10]
  - checkbox "Remember me" [checked] [ref=e13] [box=10,700,20,20]
  - button "Search" [ref=e14] [box=10,720,20,20]
"""


class TestParseKey(unittest.TestCase):
    def test_role_name_attributes_and_inline_text(self):
        self.assertEqual(
            pe.parse_key('link "Home" [ref=e5] [cursor=pointer]: Home'),
            ("link", "Home", {"ref": "e5", "cursor": "pointer"}, "Home"),
        )

    def test_flag_attributes_and_children_colon(self):
        role, name, attrs, text = pe.parse_key('checkbox "Remember me" [checked] [disabled] [ref=e2]:')
        self.assertEqual((role, name, text), ("checkbox", "Remember me", ""))
        self.assertEqual(attrs, {"checked": "true", "disabled": "true", "ref": "e2"})

    def test_json_escapes_and_colons_inside_the_name(self):
        self.assertEqual(pe.parse_key('button "Say \\"hi\\": now" [ref=e1]')[1], 'Say "hi": now')

    def test_yaml_quoted_key(self):
        self.assertEqual(pe.parse_key("'button \"a: b\" [ref=e1]': text")[:2], ("button", "a: b"))

    def test_role_without_name_and_garbage(self):
        self.assertEqual(pe.parse_key("separator")[:2], ("separator", ""))
        self.assertIsNone(pe.parse_key('"just a string"'))
        self.assertIsNone(pe.parse_key("link unexpected words"))


class TestParseSnapshot(unittest.TestCase):
    def test_parents_texts_and_props(self):
        nodes = pe.parse_snapshot(SNAP)
        by_ref = {n.ref: n for n in nodes}
        self.assertEqual(nodes[by_ref["e2"].parent].ref, "e1")
        self.assertEqual(by_ref["e2"].props, {"url": "/"})
        self.assertEqual(by_ref["e8"].texts, ["Card title"])
        self.assertEqual(by_ref["e10"].props["placeholder"], "Add a comment")
        self.assertEqual(by_ref["e6"].box, (10.0, 900.0, 300.0, 40.0))

    def test_empty(self):
        self.assertEqual(pe.parse_snapshot(""), [])
        self.assertEqual(pe.parse_snapshot(None), [])


class TestPageElements(unittest.TestCase):
    def setUp(self):
        self.els = pe.page_elements(pe.parse_snapshot(SNAP), viewport_height=800, page_key=7)
        self.by_name = {(e.type, e.name): e for e in self.els}

    def test_interactive_roles_mapped_to_our_types(self):
        self.assertIn(("Hyperlink", "YouTube Home"), self.by_name)
        self.assertIn(("ComboBox", "Search"), self.by_name)
        self.assertIn(("Button", "Search"), self.by_name)
        self.assertEqual(self.by_name[("ComboBox", "Search")].ref, "e3")
        self.assertEqual(self.by_name[("ComboBox", "Search")].page_key, 7)

    def test_outer_clickable_container_named_from_its_text(self):
        card = self.by_name[("Clickable", "Card title")]
        self.assertEqual(card.ref, "e7")  # not the inner e8: the pointer cursor is inherited
        self.assertEqual(sum(1 for e in self.els if e.type == "Clickable"), 1)

    def test_unnamed_field_uses_its_placeholder(self):
        self.assertEqual(self.by_name[("Edit", "Add a comment")].placeholder, "Add a comment")

    def test_zero_size_dropped_and_duplicates_kept_once(self):
        self.assertNotIn(("Button", "Hidden"), self.by_name)
        self.assertEqual([e.ref for e in self.els if e.name == "Search" and e.type == "Button"], ["e4"])

    def test_position_and_state_hints(self):
        self.assertEqual(self.by_name[("Hyperlink", "lofi hip hop radio")].where, "below")
        self.assertEqual(self.by_name[("Hyperlink", "Home")].where, "above")
        self.assertEqual(self.by_name[("Hyperlink", "YouTube Home")].where, "")
        box = self.by_name[("CheckBox", "Remember me")]
        self.assertTrue(box.checked)
        self.assertEqual(pe.format_line(box), f'{box.id}: CheckBox "Remember me" (checked)')

    def test_numbers_follow_page_order(self):
        self.assertEqual([e.id for e in self.els], list(range(1, len(self.els) + 1)))
        self.assertEqual(self.els[0].name, "YouTube Home")

    def test_rows_without_a_name_use_their_text(self):
        snap = '- grid [ref=e1]:\n  - row [ref=e2] [box=0,10,500,30]:\n    - gridcell "Ann" [ref=e3]\n    - gridcell "Lunch?" [ref=e4]\n'
        (row,) = pe.page_elements(pe.parse_snapshot(snap))
        self.assertEqual((row.type, row.name), ("Row", "Ann Lunch?"))

    def test_without_viewport_no_position_hint(self):
        els = pe.page_elements(pe.parse_snapshot(SNAP))
        self.assertTrue(all(e.where == "" for e in els))


class TestFormat(unittest.TestCase):
    def test_line_format_matches_the_desktop_list(self):
        el = pe.PageElement(3, "Search", "SearchBox", "searchbox", "e9")
        self.assertEqual(pe.format_line(el), '3: SearchBox "Search"')
        long = pe.PageElement(4, "x" * 80, "Hyperlink", "link", "e1", where="below", disabled=True)
        self.assertEqual(pe.format_line(long), f'4: Hyperlink "{"x" * 42}..." (below, disabled)')  # 45 max


class TestText(unittest.TestCase):
    def test_split_parts_cuts_at_spaces(self):
        parts = pe.split_parts("aaa bbb ccc ddd", 8)
        self.assertEqual(parts, ["aaa bbb", "ccc ddd"])
        self.assertEqual(pe.split_parts("", 10), [])
        self.assertEqual(pe.split_parts("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])

    def test_find_snippets_case_insensitive_and_merged(self):
        text = "Settings. " + "pad " * 50 + "Delete account here. Delete account again."
        hits = pe.find_snippets(text, "delete ACCOUNT", radius=20)
        self.assertEqual(len(hits), 1)  # the two matches overlap: one snippet
        self.assertIn("Delete account here", hits[0])
        self.assertTrue(hits[0].startswith("..."))
        self.assertEqual(pe.find_snippets(text, "missing"), [])
        self.assertEqual(pe.find_snippets(text, "  "), [])


if __name__ == "__main__":
    unittest.main()
