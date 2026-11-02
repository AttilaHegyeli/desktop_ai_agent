"""Tests for the monitor hook in SessionSetup. A fake service is passed in, so no
Ollama process, index scan or network is touched."""
import unittest
from unittest import mock

from desktop_ai_agent.core import monitors as m
from desktop_ai_agent.core.session_setup import SessionSetup


class FakeService:
    binary = None


def make(setup=None):
    return SessionSetup(setup or {}, service=FakeService())


class TestMonitorHook(unittest.TestCase):
    def setUp(self):
        saved = (m._monitors, m._preference, m._dpi_label)
        self.addCleanup(lambda: (setattr(m, "_monitors", saved[0]),
                                 setattr(m, "_preference", saved[1]),
                                 setattr(m, "_dpi_label", saved[2])))

    def fake_screen(self, rows):
        patchers = [
            mock.patch.object(m, "sys", mock.Mock(platform="win32")),
            mock.patch.object(m, "_enumerate_raw", lambda: rows),
            mock.patch.object(m, "_mss_virtual_size", lambda: None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_defaults_to_primary_when_there_is_no_screen_block(self):
        self.assertEqual(make().screen_monitor, "primary")
        self.assertEqual(make({"screen": "garbage"}).screen_monitor, "primary")

    def test_reads_the_screen_override(self):
        self.assertEqual(make({"screen": {"monitor": 1}}).screen_monitor, 1)

    def test_logs_monitor_lines_and_stores_the_monitors(self):
        self.fake_screen([{"device": "d", "left": 0, "top": 0, "right": 1920, "bottom": 1080,
                           "primary": True, "dpi": 96}])
        setup, lines = make(), []
        setup._detect_monitors(log=lines.append)
        self.assertIn("Monitors: 1 — #0 1920x1080 @100% (primary)", lines)
        self.assertEqual(len(setup.monitors), 1)
        self.assertEqual(m.target_monitor().width, 1920)

    def test_the_override_is_passed_through(self):
        self.fake_screen([
            {"device": "a", "left": 0, "top": 0, "right": 1920, "bottom": 1080, "primary": True, "dpi": 96},
            {"device": "b", "left": 1920, "top": 0, "right": 3840, "bottom": 1080, "primary": False, "dpi": 96},
        ])
        setup, lines = make({"screen": {"monitor": 1}}), []
        setup._detect_monitors(log=lines.append)
        self.assertIn("Target monitor: #1", lines)

    def test_a_crash_never_stops_setup(self):
        setup, lines = make(), []
        with mock.patch("desktop_ai_agent.core.session_setup.report_at_setup",
                        side_effect=RuntimeError("boom")):
            setup._detect_monitors(log=lines.append)
        self.assertEqual(lines, ["Monitor detection skipped (safe): boom"])
        self.assertEqual(setup.monitors, [])

    def test_no_log_function_is_fine(self):
        self.fake_screen([])
        make()._detect_monitors()


if __name__ == "__main__":
    unittest.main()