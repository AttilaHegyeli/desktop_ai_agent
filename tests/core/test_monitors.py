"""Tests for monitor and DPI detection. Logic is tested with fake Windows data; the
real Windows calls only run in the single smoke test at the bottom (Windows only)."""
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop_ai_agent.core import monitors as m

PRIMARY = {"device": r"\\.\DISPLAY1", "left": 0, "top": 0, "right": 2560, "bottom": 1440,
           "primary": True, "dpi": 144}
LEFT_OF_PRIMARY = {"device": r"\\.\DISPLAY2", "left": -1920, "top": 0, "right": 0, "bottom": 1080,
                   "primary": False, "dpi": 96}


class StateCase(unittest.TestCase):
    """Restores the module-level cache and DPI label after each test."""

    def setUp(self):
        saved = (m._monitors, m._preference, m._dpi_label)
        self.addCleanup(lambda: setattr(m, "_monitors", saved[0]))
        self.addCleanup(lambda: setattr(m, "_preference", saved[1]))
        self.addCleanup(lambda: setattr(m, "_dpi_label", saved[2]))


class TestBuildMonitors(unittest.TestCase):
    def test_single_monitor_with_scale(self):
        (mon,) = m.build_monitors([PRIMARY])
        self.assertEqual((mon.index, mon.width, mon.height, mon.scale_percent, mon.primary),
                         (0, 2560, 1440, 150, True))
        self.assertEqual((mon.right, mon.bottom), (2560, 1440))

    def test_primary_is_always_index_zero_even_with_negative_positions(self):
        mons = m.build_monitors([LEFT_OF_PRIMARY, PRIMARY])
        self.assertEqual([x.primary for x in mons], [True, False])
        self.assertEqual([x.index for x in mons], [0, 1])
        self.assertEqual((mons[1].left, mons[1].width, mons[1].scale_percent), (-1920, 1920, 100))

    def test_scale_percentages(self):
        for dpi, expected in ((96, 100), (120, 125), (144, 150), (192, 200)):
            (mon,) = m.build_monitors([{**PRIMARY, "dpi": dpi}])
            self.assertEqual(mon.scale_percent, expected)

    def test_missing_dpi_gives_unknown_scale(self):
        (mon,) = m.build_monitors([{**PRIMARY, "dpi": None}])
        self.assertIsNone(mon.scale_percent)

    def test_bad_rows_are_skipped(self):
        bad = [{"left": 0}, {**PRIMARY, "right": 0}, {**PRIMARY, "left": "x"}, {}]
        self.assertEqual(m.build_monitors(bad), [])
        self.assertEqual(len(m.build_monitors(bad + [PRIMARY])), 1)


class TestSelectAndGeometry(unittest.TestCase):
    def setUp(self):
        self.mons = m.build_monitors([PRIMARY, LEFT_OF_PRIMARY])

    def test_primary_and_default(self):
        self.assertTrue(m.select_monitor(self.mons).primary)
        self.assertTrue(m.select_monitor(self.mons, "primary").primary)
        self.assertTrue(m.select_monitor(self.mons, "").primary)

    def test_by_index_as_int_or_string(self):
        self.assertEqual(m.select_monitor(self.mons, 1).index, 1)
        self.assertEqual(m.select_monitor(self.mons, "1").index, 1)

    def test_out_of_range_or_junk_falls_back_to_primary(self):
        for pref in (5, "7", "left", None, True, 1.5):
            self.assertTrue(m.select_monitor(self.mons, pref).primary, msg=repr(pref))

    def test_empty_list_gives_none(self):
        self.assertIsNone(m.select_monitor([]))

    def test_virtual_desktop_bounding_box_with_negative_origin(self):
        self.assertEqual(m.virtual_desktop(self.mons), (-1920, 0, 4480, 1440))
        self.assertIsNone(m.virtual_desktop([]))


class TestFormattingAndWarnings(unittest.TestCase):
    def test_single_monitor_line(self):
        mons = m.build_monitors([PRIMARY])
        self.assertEqual(m.format_monitors(mons), "Monitors: 1 — #0 2560x1440 @150% (primary)")

    def test_multi_monitor_line_shows_offset(self):
        mons = m.build_monitors([PRIMARY, LEFT_OF_PRIMARY])
        self.assertEqual(
            m.format_monitors(mons),
            "Monitors: 2 — #0 2560x1440 @150% (primary), #1 1920x1080 @100% at (-1920, 0)",
        )

    def test_none_detected_line(self):
        self.assertEqual(m.format_monitors([]), "Monitors: none detected")

    def test_unknown_scale_is_shown_and_warned(self):
        mons = m.build_monitors([{**PRIMARY, "dpi": None}])
        self.assertIn("@scale unknown", m.format_monitors(mons))
        self.assertTrue(any("unknown" in w for w in m.monitor_warnings(mons)))

    def test_mixed_scales_warn(self):
        warnings = m.monitor_warnings(m.build_monitors([PRIMARY, LEFT_OF_PRIMARY]))
        self.assertTrue(any("different scale" in w and "100%" in w and "150%" in w for w in warnings))

    def test_clean_single_monitor_has_no_warnings(self):
        self.assertEqual(m.monitor_warnings(m.build_monitors([PRIMARY])), [])
        self.assertEqual(m.monitor_warnings([]), [])

    def test_no_primary_warns(self):
        mons = m.build_monitors([{**PRIMARY, "primary": False}])
        self.assertTrue(any("no primary" in w for w in m.monitor_warnings(mons)))


class TestCaptureMismatch(unittest.TestCase):
    def setUp(self):
        self.mons = m.build_monitors([PRIMARY])

    def test_matching_sizes_give_none(self):
        self.assertIsNone(m.capture_mismatch(self.mons, (2560, 1440)))

    def test_mismatch_names_both_sizes(self):
        text = m.capture_mismatch(self.mons, (1707, 960))
        self.assertIn("2560x1440", text)
        self.assertIn("1707x960", text)
        self.assertIn("DPI", text)

    def test_check_is_skipped_when_capture_size_unavailable(self):
        with mock.patch.object(m, "_mss_virtual_size", lambda: None):
            self.assertIsNone(m.capture_mismatch(self.mons))

    def test_uses_mss_size_when_none_is_given(self):
        with mock.patch.object(m, "_mss_virtual_size", lambda: (100, 100)):
            self.assertIn("100x100", m.capture_mismatch(self.mons))

    def test_no_monitors_gives_none(self):
        self.assertIsNone(m.capture_mismatch([], (1, 1)))


class TestDetect(StateCase):
    def test_non_windows_returns_empty_with_warning(self):
        warnings = []
        with mock.patch.object(m, "sys", SimpleNamespace(platform="linux")):
            self.assertEqual(m.detect_monitors(warnings=warnings), [])
        self.assertTrue(any("only available on Windows" in w for w in warnings))

    def test_windows_path_builds_monitors(self):
        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_enumerate_raw", lambda: [PRIMARY, LEFT_OF_PRIMARY]):
            found = m.detect_monitors()
        self.assertEqual(len(found), 2)

    def test_windows_failure_never_raises(self):
        def boom():
            raise OSError("access denied")

        warnings = []
        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_enumerate_raw", boom):
            self.assertEqual(m.detect_monitors(warnings=warnings), [])
        self.assertTrue(any("access denied" in w for w in warnings))

    def test_windows_reporting_nothing_warns(self):
        warnings = []
        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_enumerate_raw", lambda: []):
            self.assertEqual(m.detect_monitors(warnings=warnings), [])
        self.assertTrue(any("no monitors" in w for w in warnings))


class TestCache(StateCase):
    def test_set_get_and_target(self):
        mons = m.build_monitors([PRIMARY, LEFT_OF_PRIMARY])
        m.set_monitors(mons, preference=1)
        self.assertEqual(m.get_monitors(), mons)
        self.assertEqual(m.target_monitor().index, 1)

    def test_get_returns_a_copy(self):
        m.set_monitors(m.build_monitors([PRIMARY]))
        m.get_monitors().clear()
        self.assertEqual(len(m.get_monitors()), 1)

    def test_refresh_re_detects(self):
        m.set_monitors(m.build_monitors([PRIMARY]))
        with mock.patch.object(m, "detect_monitors", lambda **kw: m.build_monitors([PRIMARY, LEFT_OF_PRIMARY])):
            self.assertEqual(len(m.get_monitors(refresh=True)), 2)
        self.assertEqual(len(m.get_monitors()), 2)

    def test_first_use_detects_lazily(self):
        m._monitors = None
        with mock.patch.object(m, "detect_monitors", lambda **kw: m.build_monitors([PRIMARY])):
            self.assertEqual(len(m.get_monitors()), 1)


class TestDpiAwareness(StateCase):
    def test_non_windows(self):
        with mock.patch.object(m, "sys", SimpleNamespace(platform="linux")):
            self.assertIn("not applicable", m.enable_dpi_awareness())
        self.assertIn("not applicable", m.dpi_awareness_label())

    def test_windows_label_is_remembered(self):
        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_apply_dpi_awareness", lambda: "per-monitor v2"):
            self.assertEqual(m.enable_dpi_awareness(), "per-monitor v2")
        self.assertEqual(m.dpi_awareness_label(), "per-monitor v2")

    def test_windows_failure_never_raises(self):
        def boom():
            raise OSError("nope")

        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_apply_dpi_awareness", boom):
            self.assertEqual(m.enable_dpi_awareness(), "unavailable")


class TestReportAtSetup(StateCase):
    def run_report(self, raw, *, preference="primary", dpi="per-monitor v2", capture=None):
        lines = []
        m._dpi_label = dpi
        with mock.patch.object(m, "sys", SimpleNamespace(platform="win32")), \
                mock.patch.object(m, "_enumerate_raw", lambda: raw), \
                mock.patch.object(m, "_mss_virtual_size", lambda: capture):
            found = m.report_at_setup(lines.append, preference=preference)
        return found, lines

    def test_normal_single_monitor_output(self):
        found, lines = self.run_report([PRIMARY], capture=(2560, 1440))
        self.assertEqual(lines, ["DPI awareness: per-monitor v2",
                                 "Monitors: 1 — #0 2560x1440 @150% (primary)"])
        self.assertEqual(m.get_monitors(), found)

    def test_second_monitor_target_is_logged(self):
        _, lines = self.run_report([PRIMARY, LEFT_OF_PRIMARY], preference=1, capture=(4480, 1440))
        self.assertIn("Target monitor: #1", lines)

    def test_missing_requested_monitor_warns_and_uses_primary(self):
        _, lines = self.run_report([PRIMARY], preference=3, capture=(2560, 1440))
        self.assertTrue(any("Requested monitor 3 not found" in l for l in lines))
        self.assertEqual(m.target_monitor().index, 0)

    def test_mismatch_and_unavailable_dpi_become_warnings(self):
        _, lines = self.run_report([PRIMARY], dpi="unavailable", capture=(1707, 960))
        self.assertTrue(any(l.startswith("WARNING:") and "mismatch" in l for l in lines))
        self.assertTrue(any(l.startswith("WARNING:") and "DPI awareness" in l for l in lines))

    def test_nothing_detected_is_reported_not_raised(self):
        found, lines = self.run_report([])
        self.assertEqual(found, [])
        self.assertIn("Monitors: none detected", lines)
        self.assertTrue(any("no monitors" in l for l in lines))


@unittest.skipUnless(sys.platform == "win32", "needs real Windows")
class TestRealWindowsSmoke(unittest.TestCase):
    def test_real_detection_returns_sane_monitors(self):
        warnings = []
        found = m.detect_monitors(warnings=warnings)
        self.assertGreaterEqual(len(found), 1, msg=str(warnings))
        self.assertTrue(found[0].primary)
        for mon in found:
            self.assertGreater(mon.width, 0)
            self.assertGreater(mon.height, 0)


if __name__ == "__main__":
    unittest.main()