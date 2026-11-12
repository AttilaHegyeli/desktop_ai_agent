"""Fullscreen click-through overlay: shaded self-view + hand skeleton."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from desktop_ai_agent.input.hand_tracker import (
    HAND_CONNECTIONS,
    HandFrame,
    HandTracker,
    Landmark,
)

_SKELETON = (0, 255, 180)
_JOINT = (255, 255, 255)
_INDEX_TIP = (255, 160, 60)  # orange-ish index tip
_THUMB_TIP = (255, 40, 40)  # bright red thumb point
_FEED_GAIN = 0.75
_PAINT_MS = 50
_RENDER_MAX_SIDE = 960
_WDA_EXCLUDEFROMCAPTURE = 0x11


LatestFn = Callable[[], HandFrame | None]


class HandOverlay:
    """Always-on-top fullscreen translucent overlay driven by a HandTracker."""

    def __init__(
        self,
        master: tk.Misc,
        tracker: HandTracker | None = None,
        *,
        latest: LatestFn | None = None,
        opacity: float = 0.42,
        show_feed: bool = True,
        feed_gain: float = _FEED_GAIN,
        track_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        self._master = master
        self._tracker = tracker
        self._latest_fn = latest
        self.opacity = max(0.12, min(0.9, float(opacity)))
        self.show_feed = bool(show_feed)
        self.feed_gain = max(0.25, min(1.0, float(feed_gain)))
        # track_rect kept for API compat; mapping is in hand_mouse (not drawn).
        self.track_rect = track_rect

        self._win: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._photo: tk.PhotoImage | None = None
        self._job: str | None = None
        self._visible = False
        self._screen_w = 0
        self._screen_h = 0
        self._frames_drawn = 0
        self.on_first_frame: Callable[[HandFrame], None] | None = None

    @property
    def visible(self) -> bool:
        return self._visible and self._win is not None

    @property
    def frames_drawn(self) -> int:
        return self._frames_drawn

    def show(self) -> None:
        if self._win is None:
            self._build()
        assert self._win is not None
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        try:
            self._win.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass
        self._visible = True
        self._win.update_idletasks()
        self._apply_click_through(self._win)
        self._exclude_from_capture(self._win)
        self._schedule()

    def hide(self) -> None:
        self._visible = False
        self._cancel_job()
        if self._win is not None:
            self._win.withdraw()

    def destroy(self) -> None:
        self.hide()
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
        self._win = None
        self._label = None
        self._photo = None

    def _build(self) -> None:
        w = int(self._master.winfo_screenwidth())
        h = int(self._master.winfo_screenheight())
        self._screen_w, self._screen_h = w, h

        win = tk.Toplevel(self._master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass
        win.configure(bg="#000000")
        win.geometry(f"{w}x{h}+0+0")
        win.withdraw()

        label = tk.Label(win, bg="#000000", borderwidth=0, highlightthickness=0)
        label.pack(fill=tk.BOTH, expand=True)

        self._win = win
        self._label = label

    def _schedule(self) -> None:
        self._cancel_job()
        if not self._visible or self._win is None:
            return
        self._paint()
        self._job = self._master.after(_PAINT_MS, self._schedule)

    def _cancel_job(self) -> None:
        if self._job is not None:
            try:
                self._master.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def _get_frame(self) -> HandFrame | None:
        if self._latest_fn is not None:
            return self._latest_fn()
        if self._tracker is not None:
            return self._tracker.latest()
        return None

    def _paint(self) -> None:
        if self._win is None or self._label is None:
            return
        frame = self._get_frame()
        if frame is None or (self.show_feed and frame.preview_rgb is None):
            return
        rgb = compose_overlay_rgb(
            frame,
            self._screen_w,
            self._screen_h,
            show_feed=self.show_feed,
            feed_gain=self.feed_gain,
            render_max_side=_RENDER_MAX_SIDE,
        )
        photo = _rgb_to_photo(rgb, master=self._win)
        self._photo = photo
        self._label.configure(image=photo)
        self._frames_drawn += 1
        if self._frames_drawn == 1 and self.on_first_frame is not None:
            try:
                self.on_first_frame(frame)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _toplevel_hwnd(win: tk.Toplevel) -> int:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(win.winfo_id())
        GA_ROOT = 2
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)

    @classmethod
    def _apply_click_through(cls, win: tk.Toplevel) -> None:  # pragma: no cover
        try:
            import ctypes

            hwnd = cls._toplevel_hwnd(win)
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except (AttributeError, OSError, ValueError, TypeError):
            pass

    @classmethod
    def _exclude_from_capture(cls, win: tk.Toplevel) -> None:  # pragma: no cover
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = cls._toplevel_hwnd(win)
            fn = ctypes.windll.user32.SetWindowDisplayAffinity
            fn.argtypes = [wintypes.HWND, wintypes.DWORD]
            fn.restype = wintypes.BOOL
            fn(hwnd, _WDA_EXCLUDEFROMCAPTURE)
        except (AttributeError, OSError, ValueError, TypeError):
            pass


def compose_overlay_rgb(
    frame: HandFrame | None,
    screen_w: int,
    screen_h: int,
    *,
    show_feed: bool = True,
    feed_gain: float = _FEED_GAIN,
    render_max_side: int = _RENDER_MAX_SIDE,
) -> Any:
    """Build fullscreen RGB for the overlay (testable without Tk)."""
    import cv2
    import numpy as np

    target_w = max(1, int(screen_w))
    target_h = max(1, int(screen_h))
    scale = min(1.0, float(render_max_side) / float(max(target_w, target_h)))
    w = max(1, int(round(target_w * scale)))
    h = max(1, int(round(target_h * scale)))
    out = np.zeros((h, w, 3), dtype=np.uint8)

    if frame is not None and show_feed and frame.preview_rgb is not None:
        feed = _fit_cover_rgb(frame.preview_rgb, w, h)
        gray = (
            0.299 * feed[:, :, 0] + 0.587 * feed[:, :, 1] + 0.114 * feed[:, :, 2]
        )
        for c in range(3):
            mixed = 0.4 * feed[:, :, c] + 0.6 * gray
            out[:, :, c] = np.clip(mixed * feed_gain, 0, 255).astype(np.uint8)

    if frame is not None and frame.hands:
        for hand in frame.hands:
            _draw_hand(out, hand.landmarks, hand.index_tip, hand.thumb_tip)

    if w != target_w or h != target_h:
        out = cv2.resize(out, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return out


def compose_pip_rgb(
    frame: HandFrame,
    width: int,
    height: int,
    *,
    feed_gain: float = _FEED_GAIN,
) -> Any:
    """Compatibility helper used by tests — same composite at an arbitrary size."""
    return compose_overlay_rgb(
        frame,
        width,
        height,
        show_feed=True,
        feed_gain=feed_gain,
        render_max_side=max(width, height),
    )


def _fit_cover_rgb(rgb: Any, tw: int, th: int) -> Any:
    import cv2
    import numpy as np

    src = np.asarray(rgb)
    if src.ndim != 3 or src.shape[2] < 3:
        return np.zeros((th, tw, 3), dtype=np.uint8)
    sh, sw = src.shape[:2]
    if sh <= 0 or sw <= 0:
        return np.zeros((th, tw, 3), dtype=np.uint8)
    scale = max(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    resized = cv2.resize(src[:, :, :3], (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = max(0, (nw - tw) // 2)
    y0 = max(0, (nh - th) // 2)
    crop = resized[y0 : y0 + th, x0 : x0 + tw]
    if crop.shape[0] != th or crop.shape[1] != tw:
        padded = np.zeros((th, tw, 3), dtype=np.uint8)
        ch, cw = crop.shape[:2]
        padded[:ch, :cw] = crop
        return padded
    return crop


def _draw_hand(
    canvas: Any,
    landmarks: tuple[Landmark, ...],
    index_tip: Landmark,
    thumb_tip: Landmark | None = None,
) -> None:
    import cv2

    h, w = canvas.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        if 0 <= a < len(pts) and 0 <= b < len(pts):
            cv2.line(canvas, pts[a], pts[b], _SKELETON, 3, lineType=cv2.LINE_AA)
    for i, p in enumerate(pts):
        if i in (4, 8):
            continue  # drawn as highlighted tips below
        cv2.circle(canvas, p, 4, _JOINT, -1, lineType=cv2.LINE_AA)
    idx = (int(index_tip.x * w), int(index_tip.y * h))
    cv2.circle(canvas, idx, 8, _INDEX_TIP, -1, lineType=cv2.LINE_AA)
    cv2.circle(canvas, idx, 11, _INDEX_TIP, 2, lineType=cv2.LINE_AA)
    thumb = thumb_tip if thumb_tip is not None else (landmarks[4] if len(landmarks) > 4 else None)
    if thumb is not None:
        th = (int(thumb.x * w), int(thumb.y * h))
        cv2.circle(canvas, th, 10, _THUMB_TIP, -1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, th, 14, _THUMB_TIP, 2, lineType=cv2.LINE_AA)


def _rgb_to_photo(rgb: Any, *, master: tk.Misc | None = None) -> tk.PhotoImage:
    import numpy as np
    from PIL import Image, ImageTk

    arr = np.asarray(rgb, dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    if master is not None:
        return ImageTk.PhotoImage(image=img, master=master)
    return ImageTk.PhotoImage(image=img)
