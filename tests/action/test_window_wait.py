"""Tests for action/tools/window_wait.py.

Windows appear on a fake clock; nothing is launched, enumerated or focused for real."""
import unittest
from unittest import mock

from desktop_ai_agent.action.tools import window_wait as ww
from desktop_ai_agent.input.windows import WindowInfo

VSCODE = WindowInfo(1, "agent_loop.py - Visual Studio Code", "Code", 1)
CHROME_TAB = WindowInfo(2, "Claude Code docs - Google Chrome", "chrome", 2)
SPOTIFY = WindowInfo(3, "Spotify Premium", "Spotify", 3)
STEAM = WindowInfo(4, "Steam", "steamwebhelper", 4)
CALC = WindowInfo(5, "Calculator", "ApplicationFrameHost", 5)


class Case(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.desktop = [VSCODE, CHROME_TAB]  # windows open right now
        self.appear = []  # (at_seconds, window) that show up after the launch
        self.launched_at = None
        self.front = []

        def enumerate_():
            shown = [w for t, w in self.appear if self.launched_at is not None and self.now >= self.launched_at + t]
            return list(self.desktop) + shown

        def sleep(seconds):
            self.now += seconds

        patches = [
            mock.patch.object(ww, "_enumerate", side_effect=enumerate_),
            mock.patch.object(ww, "_now", side_effect=lambda: self.now),
            mock.patch.object(ww, "_sleep", side_effect=sleep),
            mock.patch.object(ww, "_bring_to_front", side_effect=lambda h: self.front.append(h) or True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def launch(self, result=None):
        def run():
            self.launched_at = self.now
            return result or {"opened": "x", "path": "p"}
        return run


class TestOpenApp(Case):
    def test_waits_for_the_new_window_of_that_program(self):
        self.appear = [(1.4, SPOTIFY)]
        result = ww.open_app_window("spotify", "Spotify", self.launch({"opened": "spotify", "path": "S.exe"}))
        self.assertEqual(result, {"opened": "spotify", "path": "S.exe", "waited_seconds": 1.5,
                                  "window": "Spotify Premium", "in_front": True})
        self.assertEqual(self.front, [3])

    def test_a_helper_process_window_is_found_by_title(self):
        self.appear = [(0.5, STEAM)]  # drawn by steamwebhelper.exe, not steam.exe
        result = ww.open_app_window("steam", "steam", self.launch())
        self.assertEqual(result["window"], "Steam")

    def test_an_already_running_app_is_focused_not_launched(self):
        self.desktop.append(SPOTIFY)
        launch = mock.Mock()
        result = ww.open_app_window("spotify", "Spotify", launch)
        launch.assert_not_called()
        self.assertEqual(result, {"opened": "spotify", "already_running": True,
                                  "window": "Spotify Premium", "in_front": True})
        self.assertEqual(self.now, 0.0)  # no waiting

    def test_a_matching_title_of_another_program_is_not_already_running(self):
        # The Chrome tab "Claude Code docs" must not count as VS Code being open.
        self.desktop = [CHROME_TAB]
        self.appear = [(0.25, VSCODE)]
        result = ww.open_app_window("code", "Code", self.launch())
        self.assertEqual(result["window"], VSCODE.title)
        self.assertNotIn("already_running", result)

    def test_old_windows_never_count_as_the_new_one(self):
        self.desktop = [CHROME_TAB]  # its title matches "code", but it was there before
        result = ww.open_app_window("vs_code", "Code", self.launch())
        self.assertIsNone(result["window"])

    def test_store_apps_are_matched_by_title(self):
        self.desktop.append(CALC)
        launch = mock.Mock()
        result = ww.open_app_window("calculator", "", launch)
        launch.assert_not_called()
        self.assertTrue(result["already_running"])

    def test_timeout_still_succeeds_with_a_note(self):
        result = ww.open_app_window("tray_only", "TrayOnly", self.launch())
        self.assertEqual((result["window"], result["waited_seconds"]), (None, ww.TIMEOUT_SECONDS))
        self.assertIn("No window appeared within 10s", result["note"])
        self.assertEqual(self.front, [])


class TestOpenPage(Case):
    def test_a_changed_browser_title_counts(self):
        tab = WindowInfo(2, "YouTube - Google Chrome", "chrome", 2)

        def open_page():
            self.desktop = [VSCODE]
            self.appear = [(0.75, tab)]
            self.launched_at = self.now
            return {"opened": "https://youtube.com"}

        result = ww.open_page_window(open_page)
        self.assertEqual((result["window"], result["waited_seconds"]), ("YouTube - Google Chrome", 0.8))
        self.assertEqual(self.front, [2])

    def test_a_new_browser_window_counts(self):
        def open_page():
            self.appear = [(0.25, WindowInfo(9, "New Tab - Google Chrome", "chrome", 2))]
            self.launched_at = self.now
            return {"opened": "https://x.org"}

        self.assertEqual(ww.open_page_window(open_page)["window"], "New Tab - Google Chrome")

    def test_nothing_changes_times_out(self):
        result = ww.open_page_window(lambda: {"opened": "https://x.org"})
        self.assertIsNone(result["window"])
        self.assertIn("note", result)


if __name__ == "__main__":
    unittest.main()
