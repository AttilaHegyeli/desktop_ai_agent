"""Hand overlay compose tests — no webcam; OpenCV/numpy only when available."""
from __future__ import annotations

import unittest

from desktop_ai_agent.input.hand_tracker import (
    INDEX_TIP,
    HandFrame,
    HandLandmarks,
    Landmark,
)
from desktop_ai_agent.ui.hand_overlay import compose_overlay_rgb, compose_pip_rgb

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2  # noqa: F401

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def _hand() -> HandLandmarks:
    pts = tuple(
        Landmark(0.5 if i == INDEX_TIP else 0.2, 0.4 if i == INDEX_TIP else 0.3)
        for i in range(21)
    )
    return HandLandmarks(landmarks=pts, handedness="Left")


@unittest.skipUnless(HAS_NUMPY and HAS_CV2, "numpy + opencv required")
class TestComposeOverlay(unittest.TestCase):
    def test_empty_frame_is_black(self):
        out = compose_overlay_rgb(None, 40, 30, show_feed=True)
        self.assertEqual(out.shape, (30, 40, 3))
        self.assertEqual(int(out.max()), 0)

    def test_skeleton_draws_non_black_pixels(self):
        frame = HandFrame(
            timestamp=0.0,
            width=64,
            height=48,
            hands=(_hand(),),
            preview_rgb=None,
        )
        out = compose_overlay_rgb(frame, 80, 60, show_feed=False)
        self.assertEqual(out.shape, (60, 80, 3))
        self.assertGreater(int(out.max()), 0)

    def test_feed_shaded_when_preview_present(self):
        preview = np.full((24, 32, 3), 200, dtype=np.uint8)
        frame = HandFrame(
            timestamp=0.0,
            width=32,
            height=24,
            hands=(),
            preview_rgb=preview,
        )
        out = compose_pip_rgb(frame, 32, 24, feed_gain=0.5)
        # Dimmed feed should be below source 200, but clearly visible.
        self.assertLess(int(out.mean()), 180)
        self.assertGreater(int(out.mean()), 20)

    def test_pip_draws_skeleton(self):
        preview = np.zeros((48, 64, 3), dtype=np.uint8)
        frame = HandFrame(
            timestamp=0.0,
            width=64,
            height=48,
            hands=(_hand(),),
            preview_rgb=preview,
        )
        out = compose_pip_rgb(frame, 64, 48, feed_gain=0.8)
        self.assertGreater(int(out.max()), 0)


if __name__ == "__main__":
    unittest.main()
