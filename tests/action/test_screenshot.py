"""Unit tests for screen capture (mss/Pillow mocked — no real display needed)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


def _install_fake_capture_modules(
    *,
    shot_w: int = 12,
    shot_h: int = 8,
    n_physical: int = 2,
) -> tuple[MagicMock, MagicMock]:
    """Put fake mss + PIL into sys.modules; return (Image mock, sct mock)."""
    fake_shot = MagicMock()
    fake_shot.width = shot_w
    fake_shot.height = shot_h
    fake_shot.rgb = b"\x00" * (shot_w * shot_h * 3)

    # monitors[0] = virtual desktop; [1..] = physical
    monitors: list[dict] = [
        {"left": 0, "top": 0, "width": shot_w * n_physical, "height": shot_h},
    ]
    for i in range(n_physical):
        monitors.append(
            {"left": i * shot_w, "top": 0, "width": shot_w, "height": shot_h}
        )

    sct = MagicMock()
    sct.monitors = monitors
    sct.grab.return_value = fake_shot
    sct.__enter__.return_value = sct
    sct.__exit__.return_value = False

    mss_mod = ModuleType("mss")
    mss_mod.mss = MagicMock(return_value=sct)  # type: ignore[attr-defined]

    image_mock = MagicMock()
    pil_image = ModuleType("PIL.Image")
    pil_image.frombytes = MagicMock(return_value=image_mock)  # type: ignore[attr-defined]
    pil_mod = ModuleType("PIL")
    pil_mod.Image = pil_image  # type: ignore[attr-defined]

    sys.modules["mss"] = mss_mod
    sys.modules["PIL"] = pil_mod
    sys.modules["PIL.Image"] = pil_image
    return image_mock, sct


def _clear_fake_modules() -> None:
    for key in ("mss", "PIL", "PIL.Image"):
        sys.modules.pop(key, None)


class TestCaptureScreen(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_fake_modules()

    def test_default_captures_primary_monitor(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        image_mock, sct = _install_fake_capture_modules()
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_screen(directory=Path(tmp))
            path = Path(result["path"])
            self.assertEqual(result["width"], 12)
            self.assertEqual(result["height"], 8)
            self.assertEqual(result["scope"], "primary")
            self.assertEqual(result["monitor"], 1)
            self.assertTrue(path.name.endswith(".png"))
            self.assertEqual(path.parent, Path(tmp))
            image_mock.save.assert_called_once_with(path, format="PNG")
            sct.grab.assert_called_once_with(sct.monitors[1])

    def test_scope_all_uses_virtual_desktop(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        _, sct = _install_fake_capture_modules()
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_screen(directory=Path(tmp), scope="all")
        self.assertEqual(result["scope"], "all")
        self.assertNotIn("monitor", result)
        sct.grab.assert_called_once_with(sct.monitors[0])

    def test_scope_monitor_uses_index(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        _, sct = _install_fake_capture_modules(n_physical=2)
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_screen(directory=Path(tmp), scope="monitor", monitor=2)
        self.assertEqual(result["scope"], "monitor")
        self.assertEqual(result["monitor"], 2)
        sct.grab.assert_called_once_with(sct.monitors[2])

    def test_scope_region_uses_rectangle(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        _, sct = _install_fake_capture_modules()
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_screen(
                directory=Path(tmp),
                scope="region",
                x=10,
                y=20,
                width=100,
                height=50,
            )
        self.assertEqual(result["scope"], "region")
        self.assertEqual(result["x"], 10)
        self.assertEqual(result["y"], 20)
        sct.grab.assert_called_once_with(
            {"left": 10, "top": 20, "width": 100, "height": 50}
        )

    def test_invalid_monitor_raises(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        _install_fake_capture_modules(n_physical=1)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "between 1 and 1"):
                capture_screen(directory=Path(tmp), scope="monitor", monitor=2)

    def test_region_missing_coords_raises(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        _install_fake_capture_modules()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "scope='region' requires"):
                capture_screen(directory=Path(tmp), scope="region", x=0, y=0)

    def test_missing_deps_raise_install_hint(self):
        from desktop_ai_agent.action.tools import screenshot as mod

        blocked = {"mss", "PIL", "PIL.Image"}
        real_import = __import__

        def blocker(name, globals=None, locals=None, fromlist=(), level=0):
            if name in blocked or name.startswith("PIL."):
                raise ImportError(name)
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=blocker):
            with self.assertRaises(ImportError) as ctx:
                mod.capture_screen()
        self.assertIn(".[screenshot]", str(ctx.exception))

    def test_unique_path_adds_suffix_when_file_exists(self):
        from desktop_ai_agent.action.tools.screenshot import _unique_path

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with patch("desktop_ai_agent.action.tools.screenshot.time") as t:
                t.strftime.return_value = "20260101_120000"
                first = _unique_path(d)
                first.write_bytes(b"x")
                second = _unique_path(d)
            self.assertEqual(first.name, "20260101_120000.png")
            self.assertEqual(second.name, "20260101_120000_1.png")


if __name__ == "__main__":
    unittest.main()
