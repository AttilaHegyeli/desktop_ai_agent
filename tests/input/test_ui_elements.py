"""Tests for input/ui_elements.py with fake data.
PowerShell is never started, nothing on screen is read, and no model is called."""
import json
import threading
import unittest

from desktop_ai_agent.input.ui_elements import (
    UiElement,
    is_visible_at,
    is_user_window,
    match_element,
    position_label,
    find_element,
    format_for_model,
    list_ui_elements,
    parse_choice,
    parse_elements,
)


def row(name="OK", type_=50000, app="Paint", cls="MSPaintApp", rect=(10, 20, 50, 40), hwnd=0):
    left, top, right, bottom = rect
    return {"name": name, "type": type_, "app": app, "cls": cls, "hwnd": hwnd,
            "left": left, "top": top, "right": right, "bottom": bottom}


class TestParseElements(unittest.TestCase):
    def test_numbers_and_cleans_rows(self):
        rows = [row("  Save \n file "), row("Volume", cls="Shell_TrayWnd", app="", rect=(1700, 1040, 1740, 1080))]
        a, b = parse_elements(rows)
        self.assertEqual((a.id, a.name, a.type, a.app), (1, "Save file", "Button", "Paint"))
        self.assertEqual((b.id, b.app, b.center), (2, "taskbar", (1720, 1060)))

    def test_password_flag_and_document_type(self):
        a, b, c = parse_elements([row("Password", type_=50004) | {"password": True},
                                  row("Body", type_=50030), row("Name", type_=50004) | {"password": "yes"}])
        self.assertEqual([(e.type, e.password) for e in (a, b, c)],
                         [("Edit", True), ("Document", False), ("Edit", False)])  # only True counts

    def test_control_type_ids_are_named_and_unwanted_types_dropped(self):
        els = parse_elements([row(type_=50007), row("pane", type_=50033), row("x", type_=None)])
        self.assertEqual([(e.name, e.type) for e in els], [("OK", "ListItem")])

    def test_invisible_characters_are_removed(self):
        (el,) = parse_elements([row("System Clock, 4:52 PM, \u200e10/\u200e31/2026", cls="Progman")])
        self.assertEqual((el.name, el.app), ("System Clock, 4:52 PM, 10/31/2026", "desktop"))

    def test_empty(self):
        self.assertEqual(parse_elements([]), [])

    def test_drops_bad_tiny_outside_and_duplicate_rows(self):
        rows = [
            row("ok"),
            row("ok"),  # exact duplicate
            row("tiny", rect=(0, 0, 3, 30)),
            row("outside", rect=(5000, 0, 5050, 30)),
            {"name": "no rect", "type": 50000},
            row("bad", rect=(0, 0, "x", 10)),
            row("bool", rect=(0, 0, True, 10)),
            "not a dict",
        ]
        els = parse_elements(rows, bounds=(0, 0, 1920, 1080))
        self.assertEqual([e.name for e in els], ["ok"])

    def test_negative_coordinates_are_kept(self):
        (el,) = parse_elements([row(rect=(-1900, 10, -1850, 40))], bounds=(-1920, 0, 1920, 1080))
        self.assertEqual(el.left, -1900)


class TestHiddenFilter(unittest.TestCase):
    def test_is_visible_at(self):
        self.assertTrue(is_visible_at(5, "App", (5, "App")))  # its own window is on top
        self.assertFalse(is_visible_at(5, "App", (9, "Other")))  # covered by another window
        self.assertTrue(is_visible_at(5, "App", None))  # unknown: keep
        self.assertTrue(is_visible_at(0, "App", (9, "Other")))  # no handle: keep
        self.assertTrue(is_visible_at(5, "Progman", (7, "WorkerW")))  # desktop icons
        self.assertFalse(is_visible_at(5, "Progman", (7, "Shell_TrayWnd")))

    def test_hidden_elements_are_dropped_counted_and_ids_stay_contiguous(self):
        # window 1 (VS Code) covers the left half; window 2 (taskbar) is visible everywhere it is
        def root_at(x, y):
            return (2, "Shell_TrayWnd") if y >= 1040 else ((1, "VSCode") if x < 960 else (3, "Progman"))

        rows = [
            row("covered", hwnd=3, cls="Progman", rect=(100, 100, 150, 150)),  # icon under VS Code
            row("File", hwnd=1, cls="VSCode", rect=(0, 0, 40, 20)),
            row("Volume", hwnd=2, cls="Shell_TrayWnd", rect=(1700, 1040, 1740, 1080)),
            row("Recycle Bin", hwnd=3, cls="Progman", rect=(1300, 100, 1350, 150)),  # icon in the open
            row("hidden menu", hwnd=4, cls="Other", rect=(500, 500, 540, 520)),
        ]
        stats = {}
        els = parse_elements(rows, root_at=root_at, stats=stats)
        self.assertEqual([(e.id, e.name) for e in els], [(1, "File"), (2, "Volume"), (3, "Recycle Bin")])
        self.assertEqual(stats, {"hidden": 2})

    def test_root_at_is_asked_about_the_centre(self):
        asked = []
        parse_elements([row(hwnd=1, rect=(10, 20, 50, 40))], root_at=lambda x, y: asked.append((x, y)))
        self.assertEqual(asked, [(30, 30)])


class TestUserWindow(unittest.TestCase):
    def test_skips_our_own_hidden_minimized_and_untitled_windows(self):
        self.assertTrue(is_user_window(10, 99, True, False, 12))  # VS Code
        self.assertFalse(is_user_window(99, 99, True, False, 12))  # our chat bar
        self.assertFalse(is_user_window(10, 99, False, False, 12))  # hidden
        self.assertFalse(is_user_window(10, 99, True, True, 12))  # minimized
        self.assertFalse(is_user_window(10, 99, True, False, 0))  # untitled helper
        self.assertTrue(is_user_window(10, None, True, False, 12))  # nothing excluded


class TestFormatAndChoice(unittest.TestCase):
    def setUp(self):
        self.els = [
            UiElement(1, "Volume", "Button", "taskbar", 1740, 1040, 1780, 1080),
            UiElement(2, "", "Button", "taskbar", 0, 1040, 40, 1080),
            UiElement(3, "x" * 100, "ListItem", "desktop", 0, 0, 76, 76),
        ]
        self.screen = (0, 0, 1920, 1080)

    def test_lists_named_elements_only_with_position(self):
        text, count = format_for_model(self.els, screen=self.screen)
        lines = text.splitlines()
        self.assertEqual(count, 2)
        self.assertEqual(lines[0], '1: Button "Volume" (taskbar, bottom-right)')
        self.assertTrue(lines[1].startswith('3: ListItem "xxx'))
        self.assertIn('..." (desktop, top-left)', lines[1])  # long names are shortened

    def test_position_defaults_to_the_area_the_elements_span(self):
        text, _ = format_for_model(self.els)
        self.assertIn("(taskbar, bottom-right)", text)

    def test_position_label_grid(self):
        def at(x, y):
            return position_label(UiElement(1, "a", "Button", "", x - 5, y - 5, x + 5, y + 5), self.screen)

        self.assertEqual(at(960, 540), "center")
        self.assertEqual(at(10, 10), "top-left")
        self.assertEqual(at(960, 10), "top-center")
        self.assertEqual(at(1910, 540), "middle-right")
        self.assertEqual(at(10, 1075), "bottom-left")
        # negative screen origin (monitor left of the primary)
        self.assertEqual(position_label(UiElement(1, "a", "Button", "", -1910, 0, -1900, 10),
                                        (-1920, 0, 0, 1080)), "top-left")

    def test_max_items(self):
        _, count = format_for_model(self.els, max_items=1)
        self.assertEqual(count, 1)

    def test_match_element_after_a_rescan(self):
        old = UiElement(5, "Close", "Button", "Edge", 1880, 0, 1920, 30)
        fresh = [
            UiElement(1, "Close", "Button", "Paint", 1880, 0, 1920, 30),  # other window
            UiElement(2, "Close", "Button", "Edge", 1870, 2, 1910, 32),  # moved 10 px: same one
            UiElement(3, "Close", "Button", "Edge", 1300, 0, 1340, 30),  # far away
        ]
        self.assertEqual(match_element(old, fresh).id, 2)
        self.assertIsNone(match_element(old, fresh, tolerance=5))
        self.assertIsNone(match_element(old, [UiElement(9, "Close", "Button", "Edge", 0, 0, 40, 30)]))
        self.assertIsNone(match_element(old, []))

    def test_match_element_prefers_the_nearest(self):
        old = UiElement(5, "Tab", "TabItem", "Edge", 100, 0, 200, 30)
        fresh = [UiElement(1, "Tab", "TabItem", "Edge", 115, 0, 215, 30),
                 UiElement(2, "Tab", "TabItem", "Edge", 103, 0, 203, 30)]
        self.assertEqual(match_element(old, fresh).id, 2)

    def test_parse_choice(self):
        self.assertEqual(parse_choice('{"id": 17}'), ("id", 17))
        self.assertEqual(parse_choice('```json\n{"id":3}\n```'), ("id", 3))
        self.assertEqual(parse_choice('{"id": null}'), ("none", None))
        self.assertEqual(parse_choice("number 5"), ("invalid", None))
        self.assertEqual(parse_choice(""), ("invalid", None))

    def test_find_element(self):
        self.assertEqual(find_element(self.els, 3).id, 3)
        self.assertIsNone(find_element(self.els, 99))
        self.assertIsNone(find_element(self.els, None))


class TestListUiElements(unittest.TestCase):
    def test_scans_and_parses(self):
        asked = []

        def scanner(max_elements):
            asked.append(max_elements)
            return [row(type_=50000)]

        els = list_ui_elements(scanner=scanner, max_elements=50, visible_only=False)
        self.assertEqual((len(els), asked), (1, [50]))

    def test_visible_only_uses_root_at_and_fills_stats(self):
        stats = {}
        rows = [row("top", hwnd=1), row("under", hwnd=2, rect=(100, 100, 150, 150))]
        els = list_ui_elements(scanner=lambda m: rows, root_at=lambda x, y: (1, "App"), stats=stats)
        self.assertEqual(([e.name for e in els], stats), (["top"], {"hidden": 1}))

    def test_visible_only_off_ignores_root_at(self):
        rows = [row("a", hwnd=1), row("b", hwnd=2, rect=(100, 100, 150, 150))]
        els = list_ui_elements(scanner=lambda m: rows, visible_only=False, root_at=lambda x, y: (1, "App"))
        self.assertEqual(len(els), 2)

    def test_bounds_are_applied(self):
        els = list_ui_elements(scanner=lambda m: [row(rect=(5000, 0, 5050, 30))], bounds=(0, 0, 1920, 1080),
                               visible_only=False)
        self.assertEqual(els, [])

    def test_failures_become_readable_errors(self):
        def raises(exc):
            def scanner(_max):
                raise exc
            return scanner

        cases = [
            (raises(ImportError("comtypes")), "pip install"),
            (raises(OSError("COM refused")), "scan failed: OSError: COM refused"),
        ]
        for scanner, text in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RuntimeError, text):
                    list_ui_elements(scanner=scanner, visible_only=False)

    def test_a_hung_scan_times_out(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with self.assertRaisesRegex(RuntimeError, "longer than 0.05s"):
            list_ui_elements(scanner=lambda m: release.wait(5) and [], timeout=0.05, visible_only=False)


if __name__ == "__main__":
    unittest.main()
