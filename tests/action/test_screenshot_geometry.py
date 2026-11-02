"""Tests for the geometry fields (left, top, captured_at) of capture_screen across all
scopes. mss and Pillow are faked and restored afterwards, so no real screen is captured."""
import json
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from desktop_ai_agent.action.tools.screenshot import capture_screen

# mss layout: index 0 = whole virtual desktop, 1.. = physical monitors.
# Here: a primary 2560x1440 at the origin and a second monitor to the LEFT of it.
VIRTUAL = {"left": -1920, "top": 0, "width": 4480, "height": 1440}
PRIMARY = {"left": 0, "top": 0, "width": 2560, "height": 1440}
LEFT_MONITOR = {"left": -1920, "top": 0, "width": 1920, "height": 1080}


class FakeShot:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.rgb = b"\x00" * (width * height * 3)


class FakeMSS:
    def __init__(self, monitors):
        self.monitors = monitors

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, area):
        return FakeShot(area["width"], area["height"])


class FakeImage:
    def save(self, path, format=None):
        Path(path).write_bytes(b"PNG")


def fake_modules(monitors):
    mss = types.ModuleType("mss")
    mss.mss = lambda: FakeMSS(monitors)
    pil = types.ModuleType("PIL")
    pil_image = types.ModuleType("PIL.Image")
    pil_image.frombytes = lambda mode, size, data: FakeImage()
    pil.Image = pil_image
    return {"mss": mss, "PIL": pil, "PIL.Image": pil_image}


class TestGeometry(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def capture(self, monitors=None, **kwargs):
        monitors = monitors if monitors is not None else [VIRTUAL, PRIMARY, LEFT_MONITOR]
        with mock.patch.dict(sys.modules, fake_modules(monitors)):
            return capture_screen(self.dir, **kwargs)

    def test_primary_is_the_default_and_starts_at_the_origin(self):
        r = self.capture()
        self.assertEqual((r["scope"], r["monitor"]), ("primary", 1))
        self.assertEqual((r["left"], r["top"], r["width"], r["height"]), (0, 0, 2560, 1440))

    def test_all_uses_the_virtual_desktop_with_a_negative_origin(self):
        r = self.capture(scope="all")
        self.assertEqual((r["left"], r["top"], r["width"], r["height"]), (-1920, 0, 4480, 1440))

    def test_a_second_monitor_reports_its_own_origin(self):
        r = self.capture(scope="monitor", monitor=2)
        self.assertEqual((r["left"], r["top"], r["width"], r["height"]), (-1920, 0, 1920, 1080))
        self.assertEqual(r["monitor"], 2)

    def test_region_origin_is_the_requested_corner(self):
        r = self.capture(scope="region", x=100, y=200, width=300, height=150)
        self.assertEqual((r["left"], r["top"], r["width"], r["height"]), (100, 200, 300, 150))
        self.assertEqual((r["x"], r["y"], r["region_width"], r["region_height"]), (100, 200, 300, 150))

    def test_image_pixel_maps_to_screen_pixel(self):
        r = self.capture(scope="monitor", monitor=2)
        x, y = 100, 50
        self.assertEqual((r["left"] + x, r["top"] + y), (-1820, 50))

    def test_monitor_without_left_and_top_defaults_to_zero(self):
        r = self.capture(monitors=[{"width": 800, "height": 600}, {"width": 640, "height": 480}])
        self.assertEqual((r["left"], r["top"]), (0, 0))
        self.assertEqual((r["width"], r["height"]), (640, 480))

    def test_captured_at_is_the_capture_time(self):
        before = time.time()
        r = self.capture()
        self.assertLessEqual(before - 0.01, r["captured_at"])
        self.assertLessEqual(r["captured_at"], time.time() + 0.01)

    def test_existing_keys_are_still_there_and_the_png_is_written(self):
        r = self.capture()
        for key in ("path", "width", "height", "scope"):
            self.assertIn(key, r)
        self.assertTrue(Path(r["path"]).is_file())
        self.assertTrue(r["path"].endswith(".png"))

    def test_result_is_json_serialisable(self):
        r = self.capture()
        self.assertEqual(
            set(json.loads(json.dumps(r))),
            {"path", "width", "height", "scope", "monitor", "left", "top", "captured_at"},
        )

    def test_bad_arguments_still_raise_value_error(self):
        with self.assertRaises(ValueError):
            self.capture(scope="everything")
        with self.assertRaises(ValueError):
            self.capture(scope="monitor", monitor=9)
        with self.assertRaises(ValueError):
            self.capture(scope="region", x=1, y=1)

    def test_missing_mss_still_gives_the_install_hint(self):
        with mock.patch.dict(sys.modules, {"mss": None}):
            with self.assertRaisesRegex(ImportError, "pip install"):
                capture_screen(self.dir)


if __name__ == "__main__":
    unittest.main()