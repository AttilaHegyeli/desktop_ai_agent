"""Hand-as-mouse: tracking-rect mapping, dwell click, pinch-drag, like/dislike scroll.

Uses ``pointer_control`` (action layer), not the LLM registry.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

from desktop_ai_agent.input.hand_tracker import (
    HandFrame,
    HandLandmarks,
    HandTracker,
    Landmark,
)

StatusFn = Callable[[str], None]
ScreenSizeFn = Callable[[], tuple[int, int]]
TrackRect = tuple[float, float, float, float]  # left, top, right, bottom in 0..1

DEFAULT_PINCH_THRESHOLD = 0.07
DEFAULT_PINCH_RELEASE = 0.11
DEFAULT_POLL_HZ = 20.0
DEFAULT_TRACK_RECT: TrackRect = (0.28, 0.22, 0.72, 0.72)
# One-finger click: hold index tip still this long.
DEFAULT_CLICK_DWELL_SEC = 0.55
DEFAULT_STILL_PX = 14
# Pinch is drag-only (not click). Hold pinched this long to mouse-down.
DEFAULT_DRAG_HOLD_SEC = 1.0
DEFAULT_SCROLL_SENS = 3
DEFAULT_GESTURE_FRAMES = 3
DEFAULT_PINCH_RELEASE_FRAMES = 5


def tip_to_screen(
    tip: Landmark,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int]:
    """Map a normalized landmark to clamped screen pixels (full frame)."""
    return map_tip_to_screen(tip, screen_w, screen_h, (0.0, 0.0, 1.0, 1.0))


def map_tip_to_screen(
    tip: Landmark,
    screen_w: int,
    screen_h: int,
    rect: TrackRect,
) -> tuple[int, int]:
    """Interpolate tip from an inset tracking rect onto the full screen."""
    left, top, right, bottom = rect
    if right <= left:
        right = left + 1e-6
    if bottom <= top:
        bottom = top + 1e-6
    nx = (float(tip.x) - left) / (right - left)
    ny = (float(tip.y) - top) / (bottom - top)
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    w = max(1, int(screen_w))
    h = max(1, int(screen_h))
    x = int(round(nx * (w - 1)))
    y = int(round(ny * (h - 1)))
    return x, y


def pinch_distance(hand: HandLandmarks) -> float:
    """Normalized Euclidean distance between thumb tip and index tip."""
    a, b = hand.thumb_tip, hand.index_tip
    return math.hypot(a.x - b.x, a.y - b.y)


def pinch_edge(
    was_pinching: bool,
    distance: float,
    *,
    down_thresh: float = DEFAULT_PINCH_THRESHOLD,
    up_thresh: float = DEFAULT_PINCH_RELEASE,
) -> tuple[bool, bool]:
    """Hysteresis pinch detector. Returns ``(is_pinching, edge_down)``."""
    down = max(0.001, float(down_thresh))
    up = max(down + 0.001, float(up_thresh))
    if was_pinching:
        if distance >= up:
            return False, False
        return True, False
    if distance <= down:
        return True, True
    return False, False


def finger_extended(hand: HandLandmarks, tip_idx: int, pip_idx: int) -> bool:
    """True when tip is farther from wrist (0) than PIP — finger roughly extended."""
    wrist = hand.landmarks[0]
    tip = hand.landmarks[tip_idx]
    pip = hand.landmarks[pip_idx]
    return math.hypot(tip.x - wrist.x, tip.y - wrist.y) > math.hypot(
        pip.x - wrist.x, pip.y - wrist.y
    )


def _finger_folded(hand: HandLandmarks, tip_idx: int, pip_idx: int) -> bool:
    """Tip at or below PIP in image space (y grows downward) ≈ curled finger."""
    tip = hand.landmarks[tip_idx]
    pip = hand.landmarks[pip_idx]
    return tip.y >= pip.y - 0.02


def _fingers_folded(hand: HandLandmarks) -> bool:
    """Index/middle/ring/pinky curled (fist-like for thumbs gestures)."""
    return (
        _finger_folded(hand, 8, 6)
        and _finger_folded(hand, 12, 10)
        and _finger_folded(hand, 16, 14)
        and _finger_folded(hand, 20, 18)
    )


def is_thumbs_up(hand: HandLandmarks) -> bool:
    """Like: thumb tip clearly above wrist; other fingers folded."""
    wrist = hand.landmarks[0]
    tip = hand.thumb_tip
    return _fingers_folded(hand) and tip.y < wrist.y - 0.08


def is_thumbs_down(hand: HandLandmarks) -> bool:
    """Dislike: thumb tip clearly below wrist; other fingers folded."""
    wrist = hand.landmarks[0]
    tip = hand.thumb_tip
    return _fingers_folded(hand) and tip.y > wrist.y + 0.08


def _px_dist(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class HandMouseController:
    """Follow index tip; dwell-still = click; pinch-hold = drag; like/dislike = scroll."""

    def __init__(
        self,
        tracker: HandTracker,
        *,
        screen_size: ScreenSizeFn,
        mouse_follow: bool = True,
        pinch_threshold: float = DEFAULT_PINCH_THRESHOLD,
        pinch_release: float = DEFAULT_PINCH_RELEASE,
        poll_hz: float = DEFAULT_POLL_HZ,
        track_rect: TrackRect = DEFAULT_TRACK_RECT,
        click_dwell_sec: float = DEFAULT_CLICK_DWELL_SEC,
        still_px: float = DEFAULT_STILL_PX,
        drag_hold_sec: float = DEFAULT_DRAG_HOLD_SEC,
        scroll_ticks: int = DEFAULT_SCROLL_SENS,
        gesture_frames: int = DEFAULT_GESTURE_FRAMES,
        pinch_release_frames: int = DEFAULT_PINCH_RELEASE_FRAMES,
        on_status: StatusFn | None = None,
    ) -> None:
        self._tracker = tracker
        self._screen_size = screen_size
        self.mouse_follow = bool(mouse_follow)
        self.pinch_threshold = float(pinch_threshold)
        self.pinch_release = float(pinch_release)
        self.poll_hz = max(5.0, float(poll_hz))
        self.track_rect = track_rect
        self.click_dwell_sec = max(0.05, float(click_dwell_sec))
        self.still_px = max(4.0, float(still_px))
        self.drag_hold_sec = max(0.05, float(drag_hold_sec))
        self.scroll_ticks = max(1, int(scroll_ticks))
        self.gesture_frames = max(1, int(gesture_frames))
        self.pinch_release_frames = max(2, int(pinch_release_frames))
        self._on_status = on_status

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pinching = False
        self._dragging = False
        self._drag_armed = False
        self._pinch_t0 = 0.0
        self._pinch_xy0: tuple[int, int] | None = None
        self._open_streak = 0
        self._still_anchor: tuple[int, int] | None = None
        self._still_t0 = 0.0
        self._dwell_clicked = False
        self._like_count = 0
        self._dislike_count = 0
        self._lock = threading.Lock()
        self._last_xy: tuple[int, int] | None = None

    @property
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def last_xy(self) -> tuple[int, int] | None:
        with self._lock:
            return self._last_xy

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._pinching = False
        self._dragging = False
        self._drag_armed = False
        self._pinch_xy0 = None
        self._open_streak = 0
        self._still_anchor = None
        self._dwell_clicked = False
        self._like_count = 0
        self._dislike_count = 0
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="hand-mouse"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        self._abort_gesture()

    def _abort_gesture(self, pointer: Any | None = None) -> None:
        """Release OS button if needed and clear pinch/drag / dwell state."""
        if self._dragging:
            try:
                if pointer is None:
                    from desktop_ai_agent.action.tools import pointer_control as pointer
                xy = self.last_xy or self._pinch_xy0 or (0, 0)
                pointer.mouse_up_at(xy[0], xy[1], button="left")
            except Exception:  # noqa: BLE001
                pass
        self._dragging = False
        self._drag_armed = False
        self._pinching = False
        self._pinch_xy0 = None
        self._open_streak = 0
        self._still_anchor = None
        self._dwell_clicked = False

    def _set_last(self, xy: tuple[int, int]) -> None:
        with self._lock:
            self._last_xy = xy

    def _reset_dwell(self, xy: tuple[int, int]) -> None:
        self._still_anchor = xy
        self._still_t0 = time.perf_counter()
        self._dwell_clicked = False

    def _run(self) -> None:
        from desktop_ai_agent.action.tools import pointer_control

        interval = 1.0 / self.poll_hz
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self._tick(pointer_control)
            except Exception:  # noqa: BLE001
                pass
            elapsed = time.perf_counter() - t0
            delay = interval - elapsed
            if delay > 0:
                self._stop.wait(delay)

    def _tick(self, pointer: Any) -> None:
        frame = self._tracker.latest()
        xy = self._xy_from_frame(frame)
        if xy is None or frame is None or not frame.hands:
            if self._dragging or self._drag_armed or self._pinching:
                self._abort_gesture(pointer)
            else:
                self._still_anchor = None
                self._dwell_clicked = False
            return
        self._set_last(xy)
        x, y = xy
        hand = frame.hands[0]

        dist = pinch_distance(hand)
        up_thresh = self.pinch_release + (0.06 if self._dragging else 0.0)
        raw_pinching, edge_down = pinch_edge(
            self._pinching,
            dist,
            down_thresh=self.pinch_threshold,
            up_thresh=up_thresh,
        )

        if edge_down:
            # Freeze cursor at the pre-pinch tip so thumb motion does not yank the point.
            self._pinch_t0 = time.perf_counter()
            self._pinch_xy0 = (x, y)
            self._open_streak = 0
            self._dragging = False
            self._drag_armed = False
            self._still_anchor = None
            self._dwell_clicked = False

        if raw_pinching:
            self._open_streak = 0
            pinching = True
        elif self._pinching or self._dragging:
            self._open_streak += 1
            pinching = self._open_streak < self.pinch_release_frames
        else:
            self._open_streak = 0
            pinching = False

        if self._pinching and not pinching:
            # Pinch is drag-only — never click on release.
            if self._dragging:
                drop = self._pinch_xy0 or (x, y)
                try:
                    pointer.mouse_up_at(drop[0], drop[1], button="left")
                except Exception:  # noqa: BLE001
                    pass
            self._dragging = False
            self._drag_armed = False
            self._pinch_xy0 = None
            self._open_streak = 0
            self._reset_dwell((x, y))

        self._pinching = pinching

        if pinching and not self._dragging and self._pinch_xy0 is not None:
            held = time.perf_counter() - self._pinch_t0
            if held >= self.drag_hold_sec:
                self._drag_armed = True
                ax, ay = self._pinch_xy0
                try:
                    pointer.mouse_down_at(ax, ay, button="left")
                    self._dragging = True
                except PermissionError:
                    pass

        if self._dragging:
            # After drag starts, follow the tip so the drop target can move.
            try:
                pointer.move_cursor_at(x, y)
                self._pinch_xy0 = (x, y)
            except PermissionError:
                pass
            self._like_count = 0
            self._dislike_count = 0
            return

        if pinching:
            # Hold cursor frozen at pinch-start while waiting for drag arm.
            frozen = self._pinch_xy0 or (x, y)
            try:
                pointer.move_cursor_at(frozen[0], frozen[1])
            except PermissionError:
                pass
            self._like_count = 0
            self._dislike_count = 0
            return

        # --- One-finger dwell click (index only) ---
        if self.mouse_follow:
            try:
                pointer.move_cursor_at(x, y)
            except PermissionError:
                return

        if self._still_anchor is None or _px_dist((x, y), self._still_anchor) > self.still_px:
            self._reset_dwell((x, y))
        elif not self._dwell_clicked:
            held = time.perf_counter() - self._still_t0
            if held >= self.click_dwell_sec:
                ax, ay = self._still_anchor
                try:
                    pointer.click_at(ax, ay, button="left")
                except PermissionError:
                    pass
                self._dwell_clicked = True

        # Like = scroll up; dislike = scroll down (hold gesture).
        if is_thumbs_up(hand):
            self._like_count += 1
            self._dislike_count = 0
        elif is_thumbs_down(hand):
            self._dislike_count += 1
            self._like_count = 0
        else:
            self._like_count = 0
            self._dislike_count = 0
            return

        if self._like_count >= self.gesture_frames:
            try:
                pointer.scroll_at(x, y, self.scroll_ticks)
            except PermissionError:
                pass
        elif self._dislike_count >= self.gesture_frames:
            try:
                pointer.scroll_at(x, y, -self.scroll_ticks)
            except PermissionError:
                pass

    def _xy_from_frame(self, frame: HandFrame | None) -> tuple[int, int] | None:
        if frame is None or not frame.hands:
            return None
        w, h = self._screen_size()
        return map_tip_to_screen(frame.hands[0].index_tip, w, h, self.track_rect)


def pointing_enabled(setup: dict[str, Any] | None) -> bool:
    cam = (setup or {}).get("camera") or {}
    if not cam.get("enabled", False):
        return False
    return bool(cam.get("pointing_enabled", True))


def track_rect_from_setup(setup: dict[str, Any] | None) -> TrackRect:
    cam = (setup or {}).get("camera") or {}
    left = float(cam.get("track_left", DEFAULT_TRACK_RECT[0]))
    top = float(cam.get("track_top", DEFAULT_TRACK_RECT[1]))
    right = float(cam.get("track_right", DEFAULT_TRACK_RECT[2]))
    bottom = float(cam.get("track_bottom", DEFAULT_TRACK_RECT[3]))
    return (left, top, right, bottom)


def controller_from_setup(
    setup: dict[str, Any] | None,
    tracker: HandTracker,
    *,
    screen_size: ScreenSizeFn,
    on_status: StatusFn | None = None,
) -> HandMouseController:
    cam = (setup or {}).get("camera") or {}
    return HandMouseController(
        tracker,
        screen_size=screen_size,
        mouse_follow=bool(cam.get("mouse_follow", True)),
        pinch_threshold=float(cam.get("pinch_threshold", DEFAULT_PINCH_THRESHOLD)),
        pinch_release=float(cam.get("pinch_release", DEFAULT_PINCH_RELEASE)),
        poll_hz=float(cam.get("mouse_poll_hz", DEFAULT_POLL_HZ)),
        track_rect=track_rect_from_setup(setup),
        click_dwell_sec=float(cam.get("click_dwell_sec", DEFAULT_CLICK_DWELL_SEC)),
        still_px=float(cam.get("still_px", DEFAULT_STILL_PX)),
        drag_hold_sec=float(cam.get("drag_hold_sec", DEFAULT_DRAG_HOLD_SEC)),
        scroll_ticks=int(cam.get("scroll_ticks", cam.get("scroll_sensitivity", DEFAULT_SCROLL_SENS))),
        gesture_frames=int(
            cam.get("gesture_frames", cam.get("peace_frames", DEFAULT_GESTURE_FRAMES))
        ),
        pinch_release_frames=int(
            cam.get("pinch_release_frames", DEFAULT_PINCH_RELEASE_FRAMES)
        ),
        on_status=on_status,
    )
