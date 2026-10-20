"""Unit tests for screen capture (mss/Pillow mocked — no real display needed)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


def _install_fake_capture_modules(shot_w: int = 12, shot_h: int = 8) -> MagicMock:
    """Put fake mss + PIL into sys.modules; return the Image mock used for save()."""
    fake_shot = MagicMock()
    fake_shot.width = shot_w
    fake_shot.height = shot_h
    fake_shot.rgb = b"\x00" * (shot_w * shot_h * 3)

    sct = MagicMock()
    sct.monitors = [{}, {"left": 0, "top": 0, "width": shot_w, "height": shot_h}]
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
    return image_mock


class TestCaptureScreen(unittest.TestCase):
    def test_writes_png_and_returns_metadata(self):
        from desktop_ai_agent.action.tools.screenshot import capture_screen

        image_mock = _install_fake_capture_modules()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = capture_screen(directory=Path(tmp))
                path = Path(result["path"])
                self.assertEqual(result["width"], 12)
                self.assertEqual(result["height"], 8)
                self.assertTrue(path.name.endswith(".png"))
                self.assertEqual(path.parent, Path(tmp))
                image_mock.save.assert_called_once_with(path, format="PNG")
        finally:
            for key in ("mss", "PIL", "PIL.Image"):
                sys.modules.pop(key, None)

    def test_missing_deps_raise_install_hint(self):
        from desktop_ai_agent.action.tools import screenshot as mod

        # Ensure a fresh ImportError even if packages are installed on the machine.
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
