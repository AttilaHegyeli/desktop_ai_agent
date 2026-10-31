"""Tests for action/tools/ui_control.py (list_elements + move_mouse).

Windows is never touched: the scan, the foreground window, the monitors, the clock and
the cursor are all replaced. The real pointer never moves."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop_ai_agent.action import executor as executor_mod
from desktop_ai_agent.action.tools import ui_control
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
        self.scans = 0
        self.pointer = (900, 500)  # where the user's mouse is before we act
        self.clicks = []

        def scan(_bounds):
            self.scans += 1
            return list(self.screen)

        patches = [
            mock.patch.object(ui_control, "_scan", side_effect=scan),
            mock.patch.object(ui_control, "_foreground", side_effect=lambda: self.foreground),
            mock.patch.object(ui_control, "_monitors", side_effect=lambda: [MONITOR]),
            mock.patch.object(ui_control, "_now", side_effect=lambda: self.now),
            mock.patch.object(ui_control, "_set_cursor", side_effect=lambda x, y: self.cursor.append((x, y))),
            mock.patch.object(ui_control, "_cursor_pos", side_effect=lambda: self.pointer),
            mock.patch.object(ui_control, "_click", side_effect=lambda button, count=1: self.clicks.append(
                (self.cursor[-1] if self.cursor else None, button) if count == 1
                else (self.cursor[-1], button, count))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestListElements(Case):
    def test_active_window_only(self):
        result = ui_control.list_elements("active")
        self.assertEqual(result["count"], 1)  # the unnamed Edge button is not listed
        self.assertEqual(result["elements"], '3: Button "New Tab" (New tab - Microsoft Edge, top-left)')

    def test_desktop_in_front_means_desktop_icons(self):
        self.foreground = (999, "WorkerW")
        result = ui_control.list_elements()
        self.assertIn('5: ListItem "Recycle Bin" (desktop, top-left)', result["elements"])
        self.assertEqual(result["count"], 1)

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
        with self.assertRaisesRegex(LookupError, "Call list_elements first"):
            ui_control.click(3)
        with self.assertRaisesRegex(LookupError, "Call list_elements first"):
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


if __name__ == "__main__":
    unittest.main()
