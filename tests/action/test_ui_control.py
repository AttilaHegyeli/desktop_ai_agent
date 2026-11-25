"""Tests for action/tools/ui_control.py (list_elements + move_mouse).

Windows is never touched: the scan, the foreground window, the monitors, the clock and
the cursor are all replaced. The real pointer never moves."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop_ai_agent.action import executor as executor_mod
from desktop_ai_agent.action.tools import page_control, ui_control
from desktop_ai_agent.input.page_elements import PageElement
from desktop_ai_agent.input.ui_elements import UiElement

EDGE, TASKBAR, DESKTOP = 100, 200, 300

SCREEN = [
    UiElement(1, "Start", "Button", "taskbar", 0, 1040, 48, 1080, TASKBAR),
    UiElement(2, "Headphones: 43%", "Button", "taskbar", 1743, 1040, 1767, 1080, TASKBAR),
    UiElement(3, "New Tab", "Button", "New tab - Microsoft Edge", 300, 0, 340, 30, EDGE),
    UiElement(4, "", "Button", "New tab - Microsoft Edge", 400, 0, 440, 30, EDGE),  # unnamed
    UiElement(5, "Recycle Bin", "ListItem", "desktop", 0, 5, 76, 75, DESKTOP),
]
MONITOR = SimpleNamespace(left=0, top=0, right=1920, bottom=1080)


class Case(unittest.TestCase):
    def setUp(self):
        ui_control.reset()
        self.addCleanup(ui_control.reset)
        self.screen = list(SCREEN)
        self.now = 1000.0
        self.cursor = []
        self.foreground = (EDGE, "Chrome_WidgetWin_1")
        self.z = [TASKBAR, EDGE, DESKTOP]  # window stack, front first
        self.z_calls = 0
        self.scans = 0
        self.pointer = (900, 500)  # where the user's mouse is before we act
        self.clicks = []
        self.pasted = []

        def scan(_bounds):
            self.scans += 1
            return list(self.screen)

        patches = [
            mock.patch.object(ui_control, "_scan", side_effect=scan),
            mock.patch.object(ui_control, "_foreground", side_effect=lambda: self.foreground),
            mock.patch.object(ui_control, "_z_order", side_effect=self.read_z),
            mock.patch.object(ui_control, "_monitors", side_effect=lambda: [MONITOR]),
            mock.patch.object(ui_control, "_now", side_effect=lambda: self.now),
            mock.patch.object(ui_control, "_set_cursor", side_effect=lambda x, y: self.cursor.append((x, y))),
            mock.patch.object(ui_control, "_cursor_pos", side_effect=lambda: self.pointer),
            mock.patch.object(ui_control, "_paste", side_effect=lambda text: self.pasted.append(
                (self.clicks[-1] if self.clicks else None, text))),
            mock.patch.object(ui_control, "_click", side_effect=lambda button, count=1: self.clicks.append(
                (self.cursor[-1] if self.cursor else None, button) if count == 1
                else (self.cursor[-1], button, count))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read_z(self):
        self.z_calls += 1
        return list(self.z)


class TestWindowOrder(Case):
    def headers(self, result):
        return [ln for ln in result["elements"].splitlines() if ln.startswith("[")]

    def test_all_lists_the_front_window_first(self):
        self.z = [EDGE, DESKTOP, TASKBAR]
        result = ui_control.list_elements("all")
        self.assertEqual(self.headers(result), ["[New tab - Microsoft Edge]", "[desktop]", "[taskbar]"])

    def test_the_request_still_beats_window_order(self):
        self.z = [EDGE, DESKTOP, TASKBAR]
        result = ui_control.list_elements("all", request="recycle bin")
        self.assertEqual(result["elements"].splitlines()[:2], ["[desktop]", '5: ListItem "Recycle Bin" (top-left)'])
        self.assertEqual(self.headers(result)[1:], ["[New tab - Microsoft Edge]", "[taskbar]"])  # ties: front first

    def test_the_front_window_fills_the_cut_first(self):
        self.screen = [UiElement(i, f"Icon {i}", "ListItem", "desktop", 0, i, 10, i + 10, DESKTOP) for i in range(1, 80)]
        self.screen.append(UiElement(500, "New Tab", "Button", "New tab - Microsoft Edge", 300, 0, 340, 30, EDGE))
        self.z = [EDGE, DESKTOP]
        self.assertEqual(ui_control.list_elements("all")["elements"].splitlines()[1], '500: Button "New Tab" (top-left)')

    def test_unknown_windows_go_last_and_no_order_keeps_screen_order(self):
        edge = [e for e in SCREEN if e.hwnd == EDGE]
        self.assertEqual(ui_control.by_window_order(SCREEN, [EDGE]), edge + [e for e in SCREEN if e.hwnd != EDGE])
        self.assertEqual(ui_control.by_window_order(SCREEN, []), SCREEN)

    def test_only_area_all_reads_the_window_stack(self):
        for area in ("active", "taskbar", "desktop"):
            ui_control.list_elements(area)
        self.assertEqual(self.z_calls, 0)
        ui_control.list_elements("all")
        self.assertEqual(self.z_calls, 1)


class TestListElements(Case):
    def test_active_window_only(self):
        result = ui_control.list_elements("active")
        self.assertEqual(result["count"], 1)  # the unnamed Edge button is not listed
        self.assertEqual(result["elements"], '3: Button "New Tab" (top-left)')

    def test_desktop_in_front_means_desktop_icons(self):
        self.foreground = (999, "WorkerW")
        result = ui_control.list_elements()
        self.assertEqual(result["elements"], '5: ListItem "Recycle Bin" (top-left)')
        self.assertEqual((result["count"], result["window"]), (1, "desktop"))

    def test_the_active_window_is_named_once_and_before_the_list(self):
        self.screen += [UiElement(i, f"Tab {i}", "TabItem", "New tab - Microsoft Edge", i * 10, 0, i * 10 + 8, 30, EDGE)
                        for i in range(10, 40)]
        result = ui_control.list_elements("active")
        self.assertEqual(result["count"], 31)
        self.assertEqual(list(result), ["area", "window", "count", "elements"])
        self.assertEqual(json.dumps(result).count("New tab - Microsoft Edge"), 1)

    def test_taskbar_lines_do_not_repeat_the_area(self):
        result = ui_control.list_elements("taskbar")
        self.assertEqual(result["elements"].splitlines(),
                         ['1: Button "Start" (bottom-left)', '2: Button "Headphones: 43%" (bottom-right)'])
        self.assertEqual(json.dumps(result).count("taskbar"), 1)  # only the area field

    def test_all_groups_elements_under_one_header_per_window(self):
        result = ui_control.list_elements("all")
        self.assertEqual(result["elements"].splitlines(), [
            "[taskbar]",
            '1: Button "Start" (bottom-left)',
            '2: Button "Headphones: 43%" (bottom-right)',
            "[New tab - Microsoft Edge]",
            '3: Button "New Tab" (top-left)',
            "[desktop]",
            '5: ListItem "Recycle Bin" (top-left)',
        ])
        self.assertEqual(result["count"], 4)  # headers are not counted
        self.assertNotIn("window", result)

    def test_numbers_from_a_grouped_list_still_work(self):
        ui_control.list_elements("all")
        ui_control.move_mouse(5)
        self.assertEqual(self.cursor, [(38, 40)])

    def test_taskbar_desktop_and_all(self):
        self.assertEqual(ui_control.list_elements("taskbar")["count"], 2)
        self.assertEqual(ui_control.list_elements("desktop")["count"], 1)
        self.assertEqual(ui_control.list_elements("all")["count"], 4)

    def test_empty_area_explains_what_to_do(self):
        self.foreground = None
        result = ui_control.list_elements("active")
        self.assertEqual(result["count"], 0)
        self.assertIn("Try another area", result["note"])

    def test_long_lists_are_cut_with_a_note(self):
        self.screen = [UiElement(i, f"Item {i}", "ListItem", "desktop", 0, i, 10, i + 10, DESKTOP)
                       for i in range(1, 201)]
        result = ui_control.list_elements("desktop")
        self.assertEqual(result["count"], ui_control.MAX_LISTED)
        self.assertIn("narrower area", result["note"])

    def test_a_match_far_down_is_moved_before_the_cut(self):
        self.screen = [UiElement(i, f"Track {i}", "ListItem", "Spotify", 0, i, 10, i + 10, EDGE)
                       for i in range(1, 140)]
        self.screen.insert(99, UiElement(500, "Liked Songs", "ListItem", "Spotify", 0, 99, 10, 109, EDGE))
        self.assertNotIn("Liked Songs", ui_control.list_elements("active")["elements"])  # cut off without it
        result = ui_control.list_elements("active", request="play my liked songs")
        self.assertEqual(result["elements"].splitlines()[0], '500: ListItem "Liked Songs" (top-left)')
        self.assertEqual(result["count"], ui_control.MAX_LISTED)
        ui_control.move_mouse(500)  # its number works
        self.assertEqual(len(self.cursor), 1)

    def test_no_match_keeps_screen_order(self):
        self.assertEqual(ui_control.list_elements("taskbar", request="the heart playlist")["elements"],
                         ui_control.list_elements("taskbar")["elements"])

    def test_through_the_executor_with_the_injected_request(self):
        ex = executor_mod.default_executor()
        plan = ex.registry.inject([{"name": "list_elements", "arguments": {"area": "taskbar"}}],
                                  {"request": "volume headphones"})
        (r,) = ex.run(plan)
        self.assertEqual(r.output["elements"].splitlines()[0], '2: Button "Headphones: 43%" (bottom-right)')

    def test_unknown_area(self):
        with self.assertRaisesRegex(ValueError, "area must be one of"):
            ui_control.list_elements("moon")


class TestMoveMouse(Case):
    def test_moves_to_the_centre_of_the_listed_element(self):
        ui_control.list_elements("taskbar")
        result = ui_control.move_mouse(2)
        self.assertEqual(self.cursor, [(1755, 1060)])
        self.assertEqual(result, {"moved_to": "Headphones: 43%", "type": "Button",
                                  "window": "taskbar", "x": 1755, "y": 1060})
        self.assertEqual(self.scans, 2)  # listed, then rescanned before moving

    def test_follows_an_element_that_shifted_a_little(self):
        ui_control.list_elements("taskbar")
        self.screen[1] = UiElement(9, "Headphones: 43%", "Button", "taskbar", 1733, 1040, 1757, 1080, TASKBAR)
        ui_control.move_mouse(2)
        self.assertEqual(self.cursor, [(1745, 1060)])

    def test_no_list_yet(self):
        with self.assertRaisesRegex(LookupError, "Call list_elements first"):
            ui_control.move_mouse(1)
        self.assertEqual(self.cursor, [])

    def test_stale_list(self):
        ui_control.list_elements("taskbar")
        self.now += ui_control.MAX_AGE_SECONDS + 1
        with self.assertRaisesRegex(LookupError, "old"):
            ui_control.move_mouse(2)
        self.assertEqual(self.cursor, [])

    def test_number_not_in_the_latest_list(self):
        ui_control.list_elements("taskbar")
        for element in (3, 4, 99):  # in the scan but not listed in this area / unnamed / unknown
            with self.subTest(element=element):
                with self.assertRaisesRegex(LookupError, "not in the latest list"):
                    ui_control.move_mouse(element)
        self.assertEqual(self.cursor, [])

    def test_element_gone_or_moved_far(self):
        ui_control.list_elements("taskbar")
        self.screen = [e for e in self.screen if e.name != "Headphones: 43%"]
        with self.assertRaisesRegex(LookupError, "moved, closed or is now hidden"):
            ui_control.move_mouse(2)
        self.assertEqual(self.cursor, [])

    def test_off_every_monitor(self):
        self.screen = [UiElement(1, "Ghost", "Button", "taskbar", 3000, 0, 3040, 30, TASKBAR)]
        ui_control.list_elements("taskbar")
        with self.assertRaisesRegex(ValueError, "not on any monitor"):
            ui_control.move_mouse(1)
        self.assertEqual(self.cursor, [])


class TestClick(Case):
    def list_and_click(self, element=3, button="left", area="active"):
        ui_control.list_elements(area)
        return ui_control.click(element, button)

    def test_left_click_on_the_centre(self):
        result = self.list_and_click()
        self.assertEqual(self.clicks, [((320, 15), "left")])  # pointer moved there first
        self.assertEqual((result["clicked"], result["button"]), ("New Tab", "left"))
        self.assertIn("list_elements again", result["note"])

    def test_right_click(self):
        self.list_and_click(2, "right", area="taskbar")
        self.assertEqual(self.clicks, [((1755, 1060), "right")])

    def test_double_click(self):
        self.screen.append(UiElement(6, "League of Legends", "ListItem", "desktop", 1748, 106, 1824, 191, DESKTOP))
        ui_control.list_elements("desktop")
        result = ui_control.click(6, double=True)
        self.assertEqual(self.clicks, [((1786, 148), "left", 2)])
        self.assertTrue(result["double"])

    def test_a_double_click_counts_as_one_for_the_limit(self):
        for _ in range(ui_control.MAX_CLICKS_PER_MINUTE):
            ui_control.list_elements()
            ui_control.click(3, double=True)
            self.now += 1.5
        ui_control.list_elements()
        with self.assertRaisesRegex(PermissionError, "Click limit"):
            ui_control.click(3)

    def test_unknown_button(self):
        ui_control.list_elements()
        with self.assertRaisesRegex(ValueError, "button must be one of"):
            ui_control.click(3, "middle")
        self.assertEqual(self.clicks, [])

    def test_the_list_is_cleared_after_a_click(self):
        self.list_and_click()
        self.now += 2
        with self.assertRaisesRegex(LookupError, "cleared by your last click"):
            ui_control.click(3)
        with self.assertRaisesRegex(LookupError, "cleared by your last click"):
            ui_control.move_mouse(3)
        self.assertEqual(len(self.clicks), 1)

    def test_minimum_gap_between_clicks(self):
        self.list_and_click()
        self.now += 0.5
        ui_control.list_elements()
        with self.assertRaisesRegex(PermissionError, "Too fast"):
            ui_control.click(3)
        self.now += 0.6  # 1.1 s after the first click
        ui_control.click(3)
        self.assertEqual(len(self.clicks), 2)

    def test_at_most_ten_clicks_per_minute(self):
        for _ in range(ui_control.MAX_CLICKS_PER_MINUTE):
            self.list_and_click()
            self.now += 1.5
        ui_control.list_elements()
        with self.assertRaisesRegex(PermissionError, "Click limit"):
            ui_control.click(3)
        self.now += 60  # the window slides: allowed again
        ui_control.list_elements()
        ui_control.click(3)
        self.assertEqual(len(self.clicks), ui_control.MAX_CLICKS_PER_MINUTE + 1)

    def test_risky_names_are_refused(self):
        self.screen.append(UiElement(6, "Delete account", "Button", "New tab - Microsoft Edge",
                                     500, 500, 600, 530, EDGE))
        ui_control.list_elements()
        with self.assertRaisesRegex(PermissionError, "looks risky .'Delete'.*ask the user"):
            ui_control.click(6)
        self.assertEqual(self.clicks, [])

    def test_risky_word_matching(self):
        for name in ("Delete", "Remove item", "Uninstall", "Send", "Pay now", "Buy", "Purchase",
                     "Format disk", "Erase", "Sign out", "Log out", "Shut down", "Restart now"):
            with self.subTest(name=name):
                self.assertIsNotNone(ui_control.risky_word(name))
        for name in ("Paint", "Sender info", "Payment history tab", "New Tab", "Headphones: 43%", ""):
            with self.subTest(name=name):
                self.assertIsNone(ui_control.risky_word(name))


class TestTypeText(Case):
    def setUp(self):
        super().setUp()
        self.screen += [
            UiElement(6, "Address and search bar", "Edit", "New tab - Microsoft Edge", 200, 40, 1200, 70, EDGE),
            UiElement(7, "Password", "Edit", "New tab - Microsoft Edge", 300, 300, 600, 330, EDGE, password=True),
            UiElement(8, "Message body", "Document", "New tab - Microsoft Edge", 300, 400, 900, 700, EDGE),
        ]
        ui_control.list_elements()

    def test_clicks_into_the_field_then_pastes(self):
        result = ui_control.type_text(6, "hello wörld ✓")
        self.assertEqual(self.pasted, [(((700, 55), "left"), "hello wörld ✓")])  # focus click first
        self.assertEqual((result["typed_into"], result["characters"]), ("Address and search bar", 13))
        self.assertIn("Enter was not pressed", result["note"])

    def test_document_fields_work_too(self):
        ui_control.type_text(8, "Hi")
        self.assertEqual(self.pasted[0][1], "Hi")

    def test_not_an_input_field(self):
        with self.assertRaisesRegex(ValueError, "is a Button, not an input field"):
            ui_control.type_text(3, "x")
        self.assertEqual((self.clicks, self.pasted), ([], []))

    def test_password_fields_are_refused(self):
        with self.assertRaisesRegex(PermissionError, "password field"):
            ui_control.type_text(7, "secret")
        self.assertEqual((self.clicks, self.pasted), ([], []))

    def test_empty_and_too_long_text(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            ui_control.type_text(6, "")
        with self.assertRaisesRegex(ValueError, "too long"):
            ui_control.type_text(6, "x" * (ui_control.MAX_TEXT + 1))
        self.assertEqual(self.pasted, [])

    def test_counts_for_the_click_rate_limit(self):
        ui_control.type_text(6, "a")
        ui_control.list_elements()
        with self.assertRaisesRegex(PermissionError, "Too fast"):
            ui_control.click(3)

    def test_typing_clears_the_list(self):
        ui_control.type_text(6, "a")
        self.now += 2
        with self.assertRaisesRegex(LookupError, "cleared by your last typing"):
            ui_control.type_text(6, "b")
        self.assertEqual(len(self.pasted), 1)

    def test_click_then_type_explains_why_it_is_refused(self):
        ui_control.click(6)
        self.now += 2
        with self.assertRaisesRegex(LookupError, "cleared by your last click.*Call list_elements"):
            ui_control.type_text(6, "aaa")
        ui_control.list_elements()  # a fresh list removes the reason
        ui_control.type_text(6, "aaa")
        self.assertEqual(self.pasted[-1][1], "aaa")

    def test_list_says_which_window_is_in_front(self):
        self.assertEqual(ui_control.list_elements()["window"], "New tab - Microsoft Edge")
        self.assertNotIn("window", ui_control.list_elements("taskbar"))
        self.foreground = None
        self.assertIsNone(ui_control.list_elements()["window"])

    def test_corner_stop_applies(self):
        self.pointer = (0, 0)
        with self.assertRaisesRegex(PermissionError, "emergency stop"):
            ui_control.type_text(6, "a")
        self.assertEqual(self.pasted, [])

    def test_through_the_executor(self):
        ex = executor_mod.default_executor()
        self.assertEqual(ex.registry.get("type_text").risk.value, "medium")
        (r,) = ex.run([{"name": "type_text", "arguments": {"element": 6, "text": "hi"}}])
        self.assertEqual(r.status, "executed")
        for args in ({"element": 6}, {"element": 6, "text": 5}, {"element": 6, "text": "x" * 2001}):
            with self.subTest(args=args):
                (r,) = ex.run([{"name": "type_text", "arguments": args}])
                self.assertEqual(r.status, "rejected")
        self.assertEqual(len(self.pasted), 1)


class TestEmergencyStop(Case):
    def test_corner_detection(self):
        for point in ((0, 0), (1919, 0), (0, 1079), (1919, 1079), (4, 3)):
            with self.subTest(point=point):
                self.assertTrue(ui_control.in_corner(*point, [MONITOR]))
        for point in ((960, 0), (0, 540), (10, 10), (2500, 0)):
            with self.subTest(point=point):
                self.assertFalse(ui_control.in_corner(*point, [MONITOR]))

    def test_corner_stops_clicks_and_moves_for_30_seconds(self):
        ui_control.list_elements()
        self.pointer = (1919, 0)  # the user slams the mouse into the top-right corner
        with self.assertRaisesRegex(PermissionError, "emergency stop"):
            ui_control.click(3)
        self.pointer = (900, 500)  # even after moving away, the lock holds
        self.now += 10
        with self.assertRaisesRegex(PermissionError, "paused for 20s more"):
            ui_control.move_mouse(3)
        with self.assertRaisesRegex(PermissionError, "paused"):
            ui_control.click(3)
        self.assertEqual((self.clicks, self.cursor), ([], []))
        self.now += ui_control.CORNER_LOCK_SECONDS
        ui_control.list_elements()
        ui_control.click(3)
        self.assertEqual(len(self.clicks), 1)

    def test_reset_clears_the_lock(self):
        ui_control.list_elements()
        self.pointer = (0, 0)
        with self.assertRaises(PermissionError):
            ui_control.move_mouse(3)
        ui_control.reset()
        self.pointer = (900, 500)
        ui_control.list_elements()
        ui_control.move_mouse(3)
        self.assertEqual(self.cursor, [(320, 15)])


class TestWiring(Case):
    def test_actions_are_registered_with_the_right_risk(self):
        ex = executor_mod.default_executor()
        self.assertEqual(ex.registry.get("list_elements").risk.value, "low")
        self.assertEqual(ex.registry.get("move_mouse").risk.value, "medium")
        self.assertEqual(ex.registry.get("click").risk.value, "medium")

    def test_executor_runs_list_then_click(self):
        results = executor_mod.default_executor().run([
            {"name": "list_elements", "arguments": {"area": "taskbar"}},
            {"name": "click", "arguments": {"element": 2, "button": "right"}},
        ])
        self.assertEqual([r.status for r in results], ["executed", "executed"])
        self.assertEqual(self.clicks, [((1755, 1060), "right")])

    def test_executor_passes_double(self):
        executor_mod.default_executor().run([
            {"name": "list_elements", "arguments": {"area": "taskbar"}},
            {"name": "click", "arguments": {"element": 1, "double": True}},
        ])
        self.assertEqual(self.clicks, [((24, 1060), "left", 2)])

    def test_bad_click_arguments_are_rejected(self):
        ex = executor_mod.default_executor()
        for args in ({"element": 2, "button": "middle"}, {"element": -1}, {"button": "left"},
                     {"element": 2, "double": "yes"}, {"element": 2, "double": 1}):
            with self.subTest(args=args):
                (r,) = ex.run([{"name": "click", "arguments": args}])
                self.assertEqual(r.status, "rejected")
        self.assertEqual(self.clicks, [])

    def test_executor_runs_list_then_move(self):
        ex = executor_mod.default_executor()
        results = ex.run([
            {"name": "list_elements", "arguments": {"area": "taskbar"}},
            {"name": "move_mouse", "arguments": {"element": 2}},
        ])
        self.assertEqual([r.status for r in results], ["executed", "executed"])
        self.assertEqual(self.cursor, [(1755, 1060)])
        self.assertIn('"moved_to": "Headphones: 43%"', results[1].to_message())

    def test_bad_arguments_are_rejected_before_anything_runs(self):
        ex = executor_mod.default_executor()
        for args in ({"element": 0}, {"element": "2"}, {"element": 2, "x": 5}, {}):
            with self.subTest(args=args):
                (r,) = ex.run([{"name": "move_mouse", "arguments": args}])
                self.assertEqual(r.status, "rejected")
        (r,) = ex.run([{"name": "list_elements", "arguments": {"area": "moon"}}])
        self.assertEqual(r.status, "rejected")
        self.assertEqual((self.cursor, self.scans), ([], 0))

    def test_errors_go_back_to_the_model(self):
        (r,) = executor_mod.default_executor().run([{"name": "move_mouse", "arguments": {"element": 2}}])
        self.assertEqual(r.status, "error")
        self.assertIn("Call list_elements first", json.loads(r.to_message())["error"])



PAGE = [
    PageElement(1, "Search", "ComboBox", "combobox", "e3", page_key=1),
    PageElement(2, "lofi hip hop radio", "Hyperlink", "link", "e6", page_key=1, where="below"),
    PageElement(3, "Accept all", "Button", "button", "e9", page_key=1),
    PageElement(4, "Post", "Button", "button", "e10", page_key=1),
]


class TestWebPages(Case):
    """Web pages go through the same tools; page_control (the browser side) is faked."""

    def setUp(self):
        super().setUp()
        self.kind = None  # what _browser_kind says about the window in front
        self.page_calls = []
        self.code_checks = []  # check_code of each page click
        self.handover_ok = True

        def list_page(request, cloud, handover_hwnd, strict):
            self.page_calls.append(("list", request, cloud, handover_hwnd, strict))
            if handover_hwnd and strict and not self.handover_ok:
                raise page_control.HandOverFailed("no web page")
            return list(PAGE), {"area": "page", "page": "YouTube", "count": len(PAGE), "elements": "..."}

        patches = [
            mock.patch.object(ui_control, "_browser_kind", side_effect=lambda hwnd: self.kind),
            mock.patch.object(ui_control, "_list_page", side_effect=list_page),
            mock.patch.object(page_control, "click", side_effect=lambda el, button, double, check_code=True: (
                self.code_checks.append(check_code)
                or self.page_calls.append(("click", el.ref, button, double)) or {"clicked": el.name, "page": "YouTube"})),
            mock.patch.object(page_control, "type_into", side_effect=lambda el, text, submit: (
                self.page_calls.append(("type", el.ref, text, submit)) or {"typed_into": el.name, "page": "YouTube"})),
            mock.patch.object(page_control, "highlight", side_effect=lambda el: (
                self.page_calls.append(("highlight", el.ref)) or {"highlighted": el.name})),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_agent_browser_in_front_lists_the_page(self):
        self.kind = "agent"
        result = ui_control.list_elements("active", request="play lofi", cloud=True)
        self.assertEqual(result["area"], "page")
        self.assertEqual(self.page_calls, [("list", "play lofi", True, None, False)])
        self.assertEqual(self.scans, 0)  # no desktop scan

    def test_area_page_works_whatever_is_in_front(self):
        result = ui_control.list_elements("page")
        self.assertEqual(result["area"], "page")

    def test_page_clicks_check_the_page_code_except_answered_cookie_buttons(self):
        ui_control.list_elements("page")
        ui_control.click(1)
        self.assertEqual(self.code_checks, [True])

    def test_users_browser_is_handed_over(self):
        self.kind = "user"
        ui_control.list_elements("active")
        self.assertEqual(self.page_calls, [("list", "", False, EDGE, True)])
        self.assertEqual(ui_control.user_browser_in_front(), EDGE)

    def test_users_browser_without_a_web_page_lists_the_window(self):
        self.kind, self.handover_ok = "user", False
        result = ui_control.list_elements("active")
        self.assertEqual(result["window"], "New tab - Microsoft Edge")
        self.assertEqual(self.scans, 1)

    def test_one_list_at_a_time(self):
        ui_control.list_elements("taskbar")
        ui_control.list_elements("page")
        with self.assertRaisesRegex(LookupError, "not in the latest list"):
            ui_control.click(5)  # the desktop list's numbers are gone
        ui_control.click(2)
        self.assertEqual(self.page_calls[-1], ("click", "e6", "left", False))
        self.assertEqual(self.clicks, [])  # no real mouse click
        self.assertEqual(self.cursor, [])
        self.now += 2  # past the click rate limit
        with self.assertRaisesRegex(LookupError, "cleared by your last click"):
            ui_control.click(2)

    def test_page_click_keeps_the_safety_rules(self):
        ui_control.list_elements("page")
        with self.assertRaisesRegex(PermissionError, "looks risky .'Post'"):
            ui_control.click(4)
        with self.assertRaisesRegex(PermissionError, "cookie"):
            ui_control.click(3, request="open youtube")
        self.now += 2
        ui_control.click(3, request="yes, accept")
        self.assertEqual(self.page_calls[-1][:2], ("click", "e9"))

    def test_type_text_on_a_page(self):
        ui_control.list_elements("page")
        ui_control.type_text(1, "lofi", submit=True)
        self.assertEqual(self.page_calls[-1], ("type", "e3", "lofi", True))
        self.assertEqual(self.pasted, [])

    def test_type_text_refuses_non_fields_and_desktop_submit(self):
        ui_control.list_elements("page")
        with self.assertRaisesRegex(ValueError, "not an input field"):
            ui_control.type_text(2, "x")
        ui_control.list_elements("taskbar")
        with self.assertRaisesRegex(ValueError, "only in search fields on web pages"):
            ui_control.type_text(1, "x", submit=True)

    def test_move_mouse_highlights_on_a_page(self):
        ui_control.list_elements("page")
        self.assertEqual(ui_control.move_mouse(2), {"highlighted": "lofi hip hop radio"})
        self.assertEqual(self.cursor, [])

    def test_web_risky_words(self):
        for name in ("Turn in", "Hand in", "Unsubmit", "Submit", "Post", "Publish", "Merge pull request", "Comment"):
            with self.subTest(name=name):
                self.assertIsNotNone(ui_control.risky_word(name))
        for name in ("Posts", "Comments", "Postcode", "Submitted work"):
            with self.subTest(name=name):
                self.assertIsNone(ui_control.risky_word(name))

if __name__ == "__main__":
    unittest.main()


class TestRiskyNamesOnWebPages(unittest.TestCase):
    def test_a_comment_placeholder_only_opens_the_box(self):
        self.assertIsNone(ui_control.risky_word("Add a comment\u2026", web=True))
        self.assertIsNone(ui_control.risky_word("Add a comment...", web=True))
        self.assertEqual(ui_control.risky_word("Add a comment\u2026"), "comment")  # desktop: unchanged
        self.assertEqual(ui_control.risky_word("Comment", web=True), "Comment")
        self.assertEqual(ui_control.risky_word("Delete comment...", web=True), "Delete")
