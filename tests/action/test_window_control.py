"""Tests for action/tools/window_control.py.

The window list, every Win32 call and the clock are fakes: no real window is listed,
focused, resized or closed."""
import unittest
from unittest import mock

from desktop_ai_agent.action import executor as executor_mod
from desktop_ai_agent.action.tools import mouse_safety, page_control, ui_control, window_control as wc
from desktop_ai_agent.action.tools.page_control import TabInfo
from desktop_ai_agent.input.windows import WindowInfo

SPOTIFY = WindowInfo(11, "Liked Songs - Spotify", "Spotify", 1)
EDGE_1 = WindowInfo(21, "GitHub - Microsoft Edge", "msedge", 2)
EDGE_2 = WindowInfo(22, "YouTube - Microsoft Edge", "msedge", 2, maximized=True)
NOTEPAD = WindowInfo(31, "notes.txt - Notepad", "notepad", 3, minimized=True)


class Case(unittest.TestCase):
    def setUp(self):
        wc.reset()
        ui_control.reset()
        self.addCleanup(wc.reset)
        self.addCleanup(ui_control.reset)
        self.wins = [SPOTIFY, EDGE_1, EDGE_2, NOTEPAD]
        self.now = 100.0
        self.calls = []
        self.alive = {w.hwnd: True for w in self.wins}
        self.closes_after = {}  # hwnd -> seconds until it disappears after WM_CLOSE (absent = never)
        self.closed_at = {}
        self.in_front = True
        self.tabs = []  # the agent browser's tabs (none: it is not open)

        def state(hwnd):
            gone = hwnd in self.closed_at and self.now >= self.closed_at[hwnd]
            alive = self.alive.get(hwnd, False) and not gone
            return alive, alive and self.calls[-1:] == [("show", hwnd, 6)], alive and self.calls[-1:] == [("show", hwnd, 3)]

        def close(hwnd):
            self.calls.append(("close", hwnd))
            if hwnd in self.closes_after:
                self.closed_at[hwnd] = self.now + self.closes_after[hwnd]

        def sleep(seconds):
            self.now += seconds

        patches = [
            mock.patch.object(wc, "_enumerate", side_effect=lambda: list(self.wins)),
            mock.patch.object(wc, "_now", side_effect=lambda: self.now),
            mock.patch.object(wc, "_sleep", side_effect=sleep),
            mock.patch.object(wc, "_show", side_effect=lambda h, c: self.calls.append(("show", h, c))),
            mock.patch.object(wc, "_state", side_effect=state),
            mock.patch.object(wc, "_bring_to_front",
                              side_effect=lambda h: self.calls.append(("front", h)) or self.in_front),
            mock.patch.object(wc, "_request_close", side_effect=close),
            mock.patch.object(wc, "_tabs", side_effect=lambda: list(self.tabs)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestMatching(unittest.TestCase):
    wins = [SPOTIFY, EDGE_1, EDGE_2, NOTEPAD]

    def names(self, query):
        return [w.hwnd for w in wc.matches(query, self.wins)]

    def test_program_title_and_word_matches(self):
        self.assertEqual(self.names("spotify"), [11])
        self.assertEqual(self.names("Notepad"), [31])
        self.assertEqual(self.names("youtube"), [22])
        self.assertEqual(self.names("liked song"), [11])  # every word, prefixes allowed
        self.assertEqual(self.names("edge"), [21, 22])  # 'edge' inside 'msedge' and in the titles
        self.assertEqual(self.names("chrome"), [])
        self.assertEqual(self.names("  "), [])

    def test_an_exact_title_or_program_wins(self):
        self.assertEqual(self.names("github - microsoft edge"), [21])
        self.assertEqual(self.names("msedge"), [21, 22])

    def test_closest(self):
        numbered = {1: SPOTIFY, 2: EDGE_1, 3: NOTEPAD}
        self.assertEqual(wc.closest("spotfy", numbered), 1)
        self.assertEqual(wc.closest("notpad", numbered), 3)
        self.assertIsNone(wc.closest("zzzz", numbered))


class TestListWindows(Case):
    def test_numbered_front_first_with_state(self):
        result = wc.list_windows()
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["windows"].splitlines(), [
            '1: "Liked Songs - Spotify" (Spotify)',
            '2: "GitHub - Microsoft Edge" (msedge)',
            '3: "YouTube - Microsoft Edge" (msedge, maximized)',
            '4: "notes.txt - Notepad" (notepad, minimized)',
        ])

    def test_long_lists_are_cut_with_a_note(self):
        self.wins = [WindowInfo(i, f"Window {i}", "app", 1) for i in range(1, 41)]
        result = wc.list_windows()
        self.assertEqual(result["count"], wc.MAX_LISTED)
        self.assertIn("Showing 30 of 40", result["note"])

    def test_empty(self):
        self.wins = []
        self.assertEqual(wc.list_windows()["note"], "No open windows.")


class TestByName(Case):
    def test_a_unique_name_acts_at_once(self):
        result = wc.window_control("minimize", name="spotify")
        self.assertEqual(self.calls, [("show", 11, 6)])
        self.assertEqual((result["window"], result["state"]), ("Liked Songs - Spotify", "minimized"))

    def test_several_matches_change_nothing_and_offer_numbers(self):
        with self.assertRaisesRegex(LookupError, "Several windows match 'edge'") as ctx:
            wc.window_control("close", name="edge")
        self.assertEqual(self.calls, [])
        self.assertIn('1: "GitHub - Microsoft Edge"', str(ctx.exception))
        self.assertIn('2: "YouTube - Microsoft Edge"', str(ctx.exception))
        wc.window_control("focus", number=2)  # the offered numbers work
        self.assertEqual(self.calls, [("front", 22)])

    def test_no_match_changes_nothing_and_suggests_the_closest(self):
        with self.assertRaisesRegex(LookupError, r"No open window matches 'spotfy'\. Closest: 1: \"Liked Songs"):
            wc.window_control("close", name="spotfy")
        self.assertEqual(self.calls, [])
        wc.window_control("focus", number=4)  # numbers now refer to all open windows
        self.assertEqual(self.calls, [("front", 31)])


class TestByNumber(Case):
    def test_needs_a_list_first(self):
        with self.assertRaisesRegex(LookupError, "Call list_windows"):
            wc.window_control("focus", number=1)

    def test_uses_the_latest_list(self):
        wc.list_windows()
        wc.window_control("maximize", number=4)
        self.assertEqual(self.calls, [("show", 31, 3)])

    def test_a_window_that_closed_or_was_replaced_is_refused(self):
        wc.list_windows()
        self.wins = [SPOTIFY, EDGE_1, WindowInfo(22, "Reused handle", "other", 9)]
        for number, text in ((3, "no longer open"), (4, "no longer open"), (9, "not in the latest list")):
            with self.subTest(number=number):
                with self.assertRaisesRegex(LookupError, text):
                    wc.window_control("focus", number=number)
        self.assertEqual(self.calls, [])

    def test_a_changed_title_is_fine(self):
        wc.list_windows()
        self.wins[0] = WindowInfo(11, "Next song - Spotify", "Spotify", 1)
        self.assertEqual(wc.window_control("focus", number=1)["window"], "Next song - Spotify")

    def test_stale_list(self):
        wc.list_windows()
        self.now += wc.MAX_AGE_SECONDS + 1
        with self.assertRaisesRegex(LookupError, "old"):
            wc.window_control("focus", number=1)

    def test_exactly_one_of_name_or_number(self):
        for kwargs in ({}, {"name": "spotify", "number": 1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "exactly one of name or number"):
                    wc.window_control("focus", **kwargs)


class TestActions(Case):
    def test_focus(self):
        result = wc.window_control("focus", name="notepad")
        self.assertEqual((self.calls, result["in_front"]), ([("front", 31)], True))
        self.assertNotIn("note", result)

    def test_focus_refused_by_windows_is_reported(self):
        self.in_front = False
        result = wc.window_control("focus", name="notepad")
        self.assertFalse(result["in_front"])
        self.assertIn("click it once", result["note"])

    def test_restore(self):
        self.assertEqual(wc.window_control("restore", name="youtube")["state"], "normal")
        self.assertEqual(self.calls, [("show", 22, 9)])

    def test_close_that_works(self):
        self.closes_after[11] = 0.3
        result = wc.window_control("close", name="spotify")
        self.assertTrue(result["closed"])
        self.assertNotIn("note", result)
        self.assertEqual(self.calls, [("close", 11)])

    def test_close_blocked_by_a_save_dialog_asks_the_user(self):
        result = wc.window_control("close", name="notepad")  # never disappears
        self.assertFalse(result["closed"])
        self.assertIn("ask them", result["note"])
        self.assertGreaterEqual(self.now, 100.0 + wc.CLOSE_WAIT_SECONDS)
        self.assertEqual(self.calls, [("close", 31)])  # nothing was saved or clicked

    def test_unknown_action(self):
        with self.assertRaisesRegex(ValueError, "action must be one of"):
            wc.window_control("delete", name="spotify")

    def test_emergency_stop_pauses_it(self):
        self.addCleanup(mouse_safety.reset)
        mouse_safety._locked_until = float("inf")
        with self.assertRaisesRegex(PermissionError, "emergency stop"):
            wc.window_control("close", name="spotify")
        self.assertEqual(self.calls, [])

    def test_an_action_makes_the_element_list_stale(self):
        ui_control._last = ui_control._LastList({}, 0.0)
        wc.window_control("focus", name="spotify")
        self.assertIsNone(ui_control._last)
        self.assertEqual(ui_control._cleared_by, "window_control")


class TestWiring(Case):
    def test_registered_with_the_right_risk_and_validated(self):
        ex = executor_mod.default_executor()
        self.assertEqual(ex.registry.get("list_windows").risk.value, "low")
        self.assertEqual(ex.registry.get("window_control").risk.value, "medium")
        for args in ({"name": "spotify"}, {"action": "delete", "name": "x"}, {"action": "close", "number": 0},
                     {"action": "close", "number": "1"}, {"action": "close", "name": "x" * 101}):
            with self.subTest(args=args):
                (r,) = ex.run([{"name": "window_control", "arguments": args}])
                self.assertEqual(r.status, "rejected")
        self.assertEqual(self.calls, [])

    def test_list_then_act_through_the_executor(self):
        results = executor_mod.default_executor().run([
            {"name": "list_windows", "arguments": {}},
            {"name": "window_control", "arguments": {"action": "minimize", "number": 1}},
        ])
        self.assertEqual([r.status for r in results], ["executed", "executed"])
        self.assertEqual(self.calls, [("show", 11, 6)])

    def test_ambiguity_goes_back_to_the_model_as_an_error(self):
        (r,) = executor_mod.default_executor().run(
            [{"name": "window_control", "arguments": {"action": "close", "name": "edge"}}])
        self.assertEqual(r.status, "error")
        self.assertIn("Several windows match", r.error)



OPERA = WindowInfo(41, "YouTube - Opera", "opera", 9)
YT_TAB = TabInfo(1, "YouTube", "https://www.youtube.com/", True, 41, 9)
GH_TAB = TabInfo(2, "GitHub", "https://github.com/", False, 41, 9)


class TestTabs(Case):
    """The agent browser's tabs share the window list and tools; page_control is faked."""

    def setUp(self):
        super().setUp()
        self.wins = [OPERA, SPOTIFY, EDGE_2]
        self.alive.update({41: True})
        self.tabs = [YT_TAB, GH_TAB]
        self.tab_calls = []
        for name, result in (("focus_tab", {"tab": "x", "in_front": True}), ("close_tab", {"tab": "x", "closed": True})):
            patcher = mock.patch.object(page_control, name, side_effect=lambda tab, n=name, r=result: (
                self.tab_calls.append((n, tab.key)) or dict(r)))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_tabs_are_listed_under_their_window(self):
        lines = wc.list_windows()["windows"].splitlines()
        self.assertEqual(lines[:3], [
            '1: "YouTube - Opera" (opera)',
            '  2: tab "YouTube" (www.youtube.com, current)',
            '  3: tab "GitHub" (github.com)',
        ])
        self.assertEqual(lines[3], '4: "Liked Songs - Spotify" (Spotify)')

    def test_a_tab_wins_over_windows_with_the_same_name(self):
        # "youtube" also matches the Opera window and Edge's YouTube window.
        result = wc.window_control("focus", name="youtube")
        self.assertEqual(self.tab_calls, [("focus_tab", 1)])
        self.assertEqual(result["action"], "focus")

    def test_switch_and_close_by_number(self):
        wc.list_windows()
        wc.window_control("focus", number=3)
        wc.window_control("close", number=2)
        self.assertEqual(self.tab_calls, [("focus_tab", 2), ("close_tab", 1)])
        self.assertEqual([c for c in self.calls if c[0] == "close"], [])  # no window got WM_CLOSE

    def test_minimize_on_a_tab_minimizes_its_window(self):
        result = wc.window_control("minimize", name="github")
        self.assertIn(("show", 41, 6), self.calls)
        self.assertEqual(result["window"], "YouTube - Opera")

    def test_a_closed_tab_number_is_refused(self):
        wc.list_windows()
        self.tabs = [YT_TAB]
        with self.assertRaisesRegex(LookupError, "no longer open"):
            wc.window_control("focus", number=3)

    def test_unreadable_tabs_are_reported(self):
        self.tabs = []
        with mock.patch.object(wc, "_tab_problem", return_value="Browser: Target closed"):
            result = wc.list_windows()
        self.assertIn("tabs could not be read (Browser: Target closed)", result["note"])

    def test_several_tabs_match_nothing_changes(self):
        # Neither title is exactly "youtube" (an exact title would win outright).
        self.tabs = [TabInfo(1, "lofi radio - YouTube", "https://www.youtube.com/watch", True, 41, 9),
                     TabInfo(3, "YouTube Music", "https://music.youtube.com/", False, 41, 9)]
        with self.assertRaisesRegex(LookupError, "Several windows match"):
            wc.window_control("close", name="youtube")
        self.assertEqual(self.tab_calls, [])

if __name__ == "__main__":
    unittest.main()
