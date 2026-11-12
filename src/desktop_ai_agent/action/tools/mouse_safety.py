"""Shared mouse safety: emergency corner lock and click rate limits."""
from __future__ import annotations

import time
from typing import Any

from desktop_ai_agent.action.tools import mouse_win32

MIN_CLICK_GAP = 1.0
MAX_CLICKS_PER_MINUTE = 10
CORNER_PX = 5
CORNER_LOCK_SECONDS = 30.0
MAX_TEXT = 2000
BUTTONS = mouse_win32.BUTTONS

_click_times: list[float] = []
_locked_until = 0.0


def reset() -> None:
    """Clear click history and emergency lock (tests; new chat)."""
    global _locked_until
    _click_times.clear()
    _locked_until = 0.0


def now() -> float:
    return time.monotonic()


def monitors() -> list[Any]:
    from desktop_ai_agent.core.monitors import get_monitors

    return get_monitors(refresh=True)


def in_corner(x: int, y: int, monitor_list: list[Any], margin: int = CORNER_PX) -> bool:
    """Is (x, y) within ``margin`` px of a corner of any monitor?"""
    for m in monitor_list:
        near_x = x - m.left <= margin or (m.right - 1) - x <= margin
        near_y = y - m.top <= margin or (m.bottom - 1) - y <= margin
        inside = m.left <= x < m.right and m.top <= y < m.bottom
        if inside and near_x and near_y:
            return True
    return False


def check_lock_only() -> None:
    """Refuse mouse actions while the emergency lock is already active."""
    t = now()
    if t < _locked_until:
        raise PermissionError(
            f"Mouse control is paused for {_locked_until - t:.0f}s more (emergency stop). "
            "Tell the user, and do not retry."
        )


def check_emergency_stop(
    monitor_list: list[Any] | None = None, *, arm_corner: bool = True
) -> None:
    """Refuse mouse actions while locked, or if the pointer is in a corner.

    ``arm_corner=False`` only enforces an existing lock (hand cursor follow hits
    screen edges constantly and must not trigger the 30s emergency pause).
    """
    global _locked_until
    check_lock_only()
    if not arm_corner:
        return
    mons = monitor_list if monitor_list is not None else monitors()
    t = now()
    if in_corner(*mouse_win32.cursor_pos(), mons):
        _locked_until = t + CORNER_LOCK_SECONDS
        raise PermissionError(
            "The user pushed the mouse into a screen corner (emergency stop). Mouse control is "
            f"paused for {CORNER_LOCK_SECONDS:.0f}s. Stop and tell the user."
        )


def check_rate() -> float:
    """Enforce click rate limits. Returns the time now."""
    t = now()
    _click_times[:] = [c for c in _click_times if t - c < 60.0]
    if _click_times and t - _click_times[-1] < MIN_CLICK_GAP:
        raise PermissionError(f"Too fast: wait at least {MIN_CLICK_GAP:g}s between clicks.")
    if len(_click_times) >= MAX_CLICKS_PER_MINUTE:
        raise PermissionError(
            f"Click limit reached ({MAX_CLICKS_PER_MINUTE} per minute). Stop and tell the user."
        )
    return t


def record_click(at: float | None = None) -> None:
    _click_times.append(now() if at is None else float(at))
