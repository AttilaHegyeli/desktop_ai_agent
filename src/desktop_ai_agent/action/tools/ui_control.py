"""Point at on-screen elements found through UI Automation. Moves the pointer; never clicks.

Flow the model follows:
  1. list_elements(area)  -> numbered list of visible clickable elements (remembered here)
  2. move_mouse(element)  -> rescan, make sure that element is still the same one in the
                             same place, then put the pointer on its centre

The model only ever passes an area name or an element number. Coordinates come from
Windows, never from the model (AGENTS.md rule 6). The four thin helpers at the bottom
(_scan, _foreground, _monitors, _set_cursor) are the only parts that touch Windows,
so tests replace them and the real cursor never moves.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from desktop_ai_agent.input.ui_elements import (
    UiElement,
    format_for_model,
    match_element,
)

AREAS = ("active", "taskbar", "desktop", "all")
MAX_AGE_SECONDS = 60.0  # an element list older than this must be refreshed before acting
MAX_LISTED = 150  # keeps the reply small enough for the model's context

_DESKTOP_CLASSES = ("Progman", "WorkerW")


@dataclass
class _LastList:
    elements: dict[int, UiElement] = field(default_factory=dict)  # id -> element, listed ones only
    taken_at: float = 0.0


_last: _LastList | None = None


def reset() -> None:
    """Forget the remembered list (tests; a new chat)."""
    global _last
    _last = None


def filter_area(
    elements: list[UiElement], area: str, foreground: tuple[int, str] | None
) -> list[UiElement]:
    """Keep the elements of one area. `foreground` = (root window handle, class) in front."""
    if area == "all":
        return list(elements)
    if area in ("taskbar", "desktop"):
        return [e for e in elements if e.app == area]
    if foreground is None:
        return []
    hwnd, cls = foreground
    if cls in _DESKTOP_CLASSES:  # the desktop itself is in front
        return [e for e in elements if e.app == "desktop"]
    return [e for e in elements if e.hwnd == hwnd]


def _bounds(monitors: list[Any]) -> tuple[int, int, int, int] | None:
    if not monitors:
        return None
    return (min(m.left for m in monitors), min(m.top for m in monitors),
            max(m.right for m in monitors), max(m.bottom for m in monitors))


def _on_a_monitor(x: int, y: int, monitors: list[Any]) -> bool:
    return any(m.left <= x < m.right and m.top <= y < m.bottom for m in monitors)


def list_elements(area: str = "active") -> dict[str, Any]:
    """Scan once, keep `area`, remember the list and return it as numbered text."""
    global _last
    if area not in AREAS:
        raise ValueError(f"area must be one of {list(AREAS)}, got '{area}'")
    monitors = _monitors()
    elements = _scan(_bounds(monitors))
    picked = filter_area(elements, area, _foreground() if area == "active" else None)
    text, listed = format_for_model(picked, max_items=MAX_LISTED, screen=_bounds(monitors))

    shown = [e for e in picked if e.name][:listed]
    _last = _LastList({e.id: e for e in shown}, _now())

    result: dict[str, Any] = {"area": area, "count": listed, "elements": text}
    if not listed:
        result["note"] = (
            f"No named clickable elements in area '{area}'. Try another area "
            "(taskbar, desktop, all) or take a screenshot."
        )
    elif listed < sum(1 for e in picked if e.name):
        result["note"] = f"Only the first {listed} are shown. Use a narrower area to see the rest."
    return result


def move_mouse(element: int) -> dict[str, Any]:
    """Put the pointer on the centre of an element from the latest list. Never clicks."""
    if _last is None:
        raise LookupError("No element list yet. Call list_elements first, then use one of its numbers.")
    age = _now() - _last.taken_at
    if age > MAX_AGE_SECONDS:
        raise LookupError(
            f"The element list is {age:.0f}s old (limit {MAX_AGE_SECONDS:.0f}s). "
            "Call list_elements again, then move_mouse."
        )
    old = _last.elements.get(element)
    if old is None:
        raise LookupError(f"Element {element} is not in the latest list. Use a number from that list.")

    monitors = _monitors()
    fresh = match_element(old, _scan(_bounds(monitors)))
    if fresh is None:
        raise LookupError(
            f"Element {element} ('{old.name}') has moved, closed or is now hidden. "
            "Call list_elements again."
        )
    x, y = fresh.center
    if not _on_a_monitor(x, y, monitors):
        raise ValueError(f"Element {element} ('{old.name}') is not on any monitor ({x}, {y}).")
    _set_cursor(x, y)
    return {"moved_to": fresh.name, "type": fresh.type, "window": fresh.app, "x": x, "y": y}


# ---- Windows (thin; replaced in tests) ---------------------------------------------

def _now() -> float:
    return time.monotonic()


def _monitors() -> list[Any]:
    from desktop_ai_agent.core.monitors import get_monitors

    return get_monitors(refresh=True)  # monitors can change between turns


def _scan(bounds: tuple[int, int, int, int] | None) -> list[UiElement]:  # pragma: no cover
    from desktop_ai_agent.input.ui_elements import list_ui_elements

    return list_ui_elements(bounds=bounds)


def _foreground() -> tuple[int, str] | None:  # pragma: no cover
    import os

    from desktop_ai_agent.input.ui_elements import foreground_window

    # Our own chat bar is in front while the user types; we mean the window below it.
    return foreground_window(exclude_pid=os.getpid())


def _set_cursor(x: int, y: int) -> None:  # pragma: no cover - moves the real pointer
    import ctypes

    if not ctypes.WinDLL("user32", use_last_error=True).SetCursorPos(int(x), int(y)):
        raise OSError(f"SetCursorPos({x}, {y}) failed (error {ctypes.get_last_error()})")
