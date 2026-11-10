"""Hand tracker unit tests — no real webcam or MediaPipe runtime required."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from desktop_ai_agent.input.hand_tracker import (
    HAND_CONNECTIONS,
    INDEX_TIP,
    HandFrame,
    HandLandmarks,
    HandTracker,
    HandTrackerError,
    Landmark,
    camera_enabled,
    ensure_hand_model,
    from_setup,
    hand_tracking_available,
    skeleton_segments,
)


def _fake_hand(*, tip_x: float = 0.5, tip_y: float = 0.25) -> HandLandmarks:
    pts = []
    for i in range(21):
        if i == INDEX_TIP:
            pts.append(Landmark(tip_x, tip_y, 0.0))
        else:
            pts.append(Landmark(0.1 * (i % 5), 0.1 * (i // 5), 0.0))
    return HandLandmarks(landmarks=tuple(pts), handedness="Right")


class TestHandTrackingAvailable(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(hand_tracking_available(), bool)

    def test_false_when_import_fails(self):
        real_import = __import__

        def boom(name, *args, **kwargs):
            if name in ("cv2", "mediapipe"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=boom):
            self.assertFalse(hand_tracking_available())


class TestCameraConfig(unittest.TestCase):
    def test_camera_enabled(self):
        self.assertTrue(camera_enabled({"camera": {"enabled": True}}))
        self.assertFalse(camera_enabled({"camera": {"enabled": False}}))
        self.assertFalse(camera_enabled({}))
        self.assertFalse(camera_enabled(None))

    def test_from_setup_defaults(self):
        tracker = from_setup({"camera": {"enabled": True}})
        self.assertEqual(tracker.device_index, 0)
        self.assertEqual(tracker.max_num_hands, 2)
        self.assertTrue(tracker.mirror)

    def test_from_setup_custom(self):
        tracker = from_setup(
            {
                "camera": {
                    "device_index": 1,
                    "width": 320,
                    "height": 240,
                    "max_num_hands": 1,
                    "mirror": False,
                }
            }
        )
        self.assertEqual(tracker.device_index, 1)
        self.assertEqual(tracker.width, 320)
        self.assertEqual(tracker.height, 240)
        self.assertEqual(tracker.max_num_hands, 1)
        self.assertFalse(tracker.mirror)


class TestHandFrame(unittest.TestCase):
    def test_index_tip_none_without_hands(self):
        frame = HandFrame(timestamp=1.0, width=100, height=100, hands=())
        self.assertIsNone(frame.index_tip)

    def test_index_tip_from_first_hand(self):
        hand = _fake_hand(tip_x=0.42, tip_y=0.33)
        frame = HandFrame(timestamp=1.0, width=640, height=480, hands=(hand,))
        tip = frame.index_tip
        assert tip is not None
        self.assertAlmostEqual(tip.x, 0.42)
        self.assertAlmostEqual(tip.y, 0.33)
        self.assertIs(hand.index_tip, tip)

    def test_bad_landmark_count(self):
        with self.assertRaises(ValueError):
            HandLandmarks(landmarks=(Landmark(0, 0),))


class TestSkeletonSegments(unittest.TestCase):
    def test_segment_count_and_pixel_math(self):
        hand = _fake_hand(tip_x=1.0, tip_y=0.5)
        segs = skeleton_segments(hand.landmarks, width=200, height=100)
        self.assertEqual(len(segs), len(HAND_CONNECTIONS))
        # Index tip is landmark 8 → connection (7, 8) ends at (200, 50)
        tip_segs = [s for s in segs if s[1] == (200, 50) or s[0] == (200, 50)]
        self.assertTrue(tip_segs)


class TestHandTrackerLifecycle(unittest.TestCase):
    def test_start_requires_deps(self):
        tracker = HandTracker()
        with patch(
            "desktop_ai_agent.input.hand_tracker.hand_tracking_available",
            return_value=False,
        ):
            with self.assertRaises(HandTrackerError):
                tracker.start()

    def test_latest_starts_none(self):
        self.assertIsNone(HandTracker().latest())

    def test_ensure_hand_model_uses_existing(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hand_landmarker.task"
            path.write_bytes(b"fake-model")
            self.assertEqual(ensure_hand_model(path), path)


if __name__ == "__main__":
    unittest.main()
