"""Hand-mouse geometry / dwell-click / pinch-drag / like-dislike scroll tests."""
from __future__ import annotations

import time
import unittest
from unittest import mock

from desktop_ai_agent.input.hand_mouse import (
    HandMouseController,
    is_thumbs_down,
    is_thumbs_up,
    map_tip_to_screen,
    pinch_distance,
    pinch_edge,
    pointing_enabled,
    tip_to_screen,
)
from desktop_ai_agent.input.hand_tracker import (
    INDEX_TIP,
    THUMB_TIP,
    HandFrame,
    HandLandmarks,
    Landmark,
)


def _hand(
    *,
    index=(0.5, 0.5),
    thumb=(0.5, 0.5),
    middle=(0.5, 0.5),
    ring=(0.5, 0.6),
    pinky=(0.5, 0.6),
    wrist=(0.5, 0.9),
) -> HandLandmarks:
    pts = [Landmark(0.5, 0.7) for _ in range(21)]
    pts[0] = Landmark(float(wrist[0]), float(wrist[1]))
    pts[THUMB_TIP] = Landmark(float(thumb[0]), float(thumb[1]))
    pts[6] = Landmark(0.5, 0.65)
    pts[INDEX_TIP] = Landmark(float(index[0]), float(index[1]))
    pts[10] = Landmark(0.55, 0.65)
    pts[12] = Landmark(float(middle[0]), float(middle[1]))
    pts[14] = Landmark(0.6, 0.7)
    pts[16] = Landmark(float(ring[0]), float(ring[1]))
    pts[18] = Landmark(0.65, 0.7)
    pts[20] = Landmark(float(pinky[0]), float(pinky[1]))
    return HandLandmarks(landmarks=tuple(pts), handedness="Right")


class TestTipToScreen(unittest.TestCase):
    def test_full_frame(self):
        self.assertEqual(tip_to_screen(Landmark(0.5, 0.25), 200, 100), (100, 25))

    def test_inset_corners(self):
        rect = (0.2, 0.1, 0.8, 0.9)
        self.assertEqual(map_tip_to_screen(Landmark(0.2, 0.1), 100, 100, rect), (0, 0))
        self.assertEqual(map_tip_to_screen(Landmark(0.8, 0.9), 100, 100, rect), (99, 99))


class TestPinch(unittest.TestCase):
    def test_distance(self):
        hand = _hand(index=(0.0, 0.0), thumb=(0.3, 0.4))
        self.assertAlmostEqual(pinch_distance(hand), 0.5)

    def test_edge_hysteresis(self):
        pinching, fired = pinch_edge(False, 0.04, down_thresh=0.05, up_thresh=0.08)
        self.assertTrue(pinching and fired)


class TestLikeDislike(unittest.TestCase):
    def test_thumbs_up_and_down(self):
        like = _hand(
            thumb=(0.5, 0.3),
            index=(0.5, 0.85),
            middle=(0.55, 0.85),
            ring=(0.6, 0.85),
            pinky=(0.65, 0.85),
            wrist=(0.5, 0.7),
        )
        self.assertTrue(is_thumbs_up(like))
        self.assertFalse(is_thumbs_down(like))

        dislike = _hand(
            thumb=(0.5, 0.95),
            index=(0.5, 0.75),
            middle=(0.55, 0.75),
            ring=(0.6, 0.75),
            pinky=(0.65, 0.75),
            wrist=(0.5, 0.5),
        )
        self.assertTrue(is_thumbs_down(dislike))
        self.assertFalse(is_thumbs_up(dislike))


class TestPointingEnabled(unittest.TestCase):
    def test_requires_camera_and_flag(self):
        self.assertFalse(pointing_enabled({}))
        self.assertTrue(
            pointing_enabled({"camera": {"enabled": True, "pointing_enabled": True}})
        )


class TestHandMouseController(unittest.TestCase):
    def _ctrl(self, tracker):
        return HandMouseController(
            tracker,
            screen_size=lambda: (200, 100),
            mouse_follow=True,
            pinch_threshold=0.05,
            pinch_release=0.08,
            track_rect=(0.0, 0.0, 1.0, 1.0),
            click_dwell_sec=0.05,
            still_px=20,
            drag_hold_sec=0.08,
            gesture_frames=2,
            scroll_ticks=3,
            poll_hz=50,
        )

    def test_dwell_still_clicks_once(self):
        tracker = mock.Mock()
        still = HandFrame(0, 640, 480, (_hand(index=(0.5, 0.5), thumb=(0.7, 0.5)),))
        tracker.latest.side_effect = [still, still, still]

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl._tick(pointer)
        time.sleep(0.06)
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        pointer.click_at.assert_called_once()
        pointer.mouse_down_at.assert_not_called()

    def test_short_pinch_does_not_click(self):
        tracker = mock.Mock()
        open_h = HandFrame(0, 640, 480, (_hand(index=(0.5, 0.5), thumb=(0.7, 0.5)),))
        pinch = HandFrame(0, 640, 480, (_hand(index=(0.5, 0.5), thumb=(0.52, 0.5)),))
        frames = [open_h, pinch] + [open_h] * 6
        tracker.latest.side_effect = frames

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl.drag_hold_sec = 10.0
        ctrl.pinch_release_frames = 3
        # Prevent dwell click while open.
        ctrl.click_dwell_sec = 10.0
        for _ in frames:
            ctrl._tick(pointer)
        pointer.click_at.assert_not_called()
        pointer.mouse_down_at.assert_not_called()

    def test_hold_pinch_drags_after_delay(self):
        tracker = mock.Mock()
        open_h = HandFrame(0, 640, 480, (_hand(index=(0.3, 0.5), thumb=(0.6, 0.5)),))
        pinch = HandFrame(0, 640, 480, (_hand(index=(0.3, 0.5), thumb=(0.32, 0.5)),))
        frames = [open_h, pinch, pinch] + [open_h] * 6
        tracker.latest.side_effect = frames

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl.click_dwell_sec = 10.0
        ctrl.pinch_release_frames = 3
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        time.sleep(0.09)
        for _ in range(len(frames) - 2):
            ctrl._tick(pointer)
        pointer.mouse_down_at.assert_called()
        pointer.mouse_up_at.assert_called()
        pointer.click_at.assert_not_called()

    def test_pinch_freezes_cursor_until_drag(self):
        tracker = mock.Mock()
        open_h = HandFrame(0, 640, 480, (_hand(index=(0.3, 0.5), thumb=(0.6, 0.5)),))
        # Index jumps while pinched — cursor must stay at pinch-start until drag.
        pinch_a = HandFrame(0, 640, 480, (_hand(index=(0.3, 0.5), thumb=(0.32, 0.5)),))
        pinch_b = HandFrame(0, 640, 480, (_hand(index=(0.8, 0.8), thumb=(0.82, 0.8)),))
        tracker.latest.side_effect = [open_h, pinch_a, pinch_b]

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl.click_dwell_sec = 10.0
        ctrl.drag_hold_sec = 10.0
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        # Last move during pinch-hold should be frozen near first pinch tip.
        last_move = pointer.move_cursor_at.call_args_list[-1].args
        self.assertLess(abs(last_move[0] - 60), 5)  # 0.3 * 199 ≈ 60
        self.assertLess(abs(last_move[1] - 50), 5)

    def test_hand_loss_while_dragging_releases_button(self):
        tracker = mock.Mock()
        pinch = HandFrame(0, 640, 480, (_hand(index=(0.3, 0.5), thumb=(0.32, 0.5)),))
        empty = HandFrame(0, 640, 480, ())
        tracker.latest.side_effect = [pinch, pinch, empty]

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl.click_dwell_sec = 10.0
        ctrl.drag_hold_sec = 0.01
        ctrl._tick(pointer)
        time.sleep(0.02)
        ctrl._tick(pointer)
        self.assertTrue(ctrl._dragging)
        pointer.mouse_down_at.assert_called()
        ctrl._tick(pointer)
        pointer.mouse_up_at.assert_called()
        self.assertFalse(ctrl._dragging)
        self.assertFalse(ctrl._pinching)

    def test_like_scrolls_up_dislike_down(self):
        like = HandFrame(
            0,
            640,
            480,
            (
                _hand(
                    thumb=(0.5, 0.3),
                    index=(0.5, 0.85),
                    middle=(0.55, 0.85),
                    ring=(0.6, 0.85),
                    pinky=(0.65, 0.85),
                    wrist=(0.5, 0.7),
                ),
            ),
        )
        dislike = HandFrame(
            0,
            640,
            480,
            (
                _hand(
                    thumb=(0.5, 0.95),
                    index=(0.5, 0.75),
                    middle=(0.55, 0.75),
                    ring=(0.6, 0.75),
                    pinky=(0.65, 0.75),
                    wrist=(0.5, 0.5),
                ),
            ),
        )
        tracker = mock.Mock()
        tracker.latest.side_effect = [like, like, dislike, dislike]

        pointer = mock.Mock()
        ctrl = self._ctrl(tracker)
        ctrl.click_dwell_sec = 10.0
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        ctrl._tick(pointer)
        calls = [c.args[2] for c in pointer.scroll_at.call_args_list]
        self.assertIn(3, calls)
        self.assertIn(-3, calls)


if __name__ == "__main__":
    unittest.main()
