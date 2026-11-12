"""Point at and click on-screen elements found through UI Automation (LLM path).

Flow the model follows:
  1. list_elements(area)   -> numbered list of visible clickable elements (remembered here)
  2. move_mouse(element)   -> rescan, then put the pointer on its centre (and click)

The model only ever passes an area name, an element number and a mouse button.
Absolute-coordinate helpers live in ``pointer_control`` (hand / sensors).

Safety (shared with pointer_control via mouse_safety):
  - after a click the element list is cleared
  - click rate limits and emergency corner stop
  - risky element names refused; type_text never presses Enter
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from desktop_ai_agent.action.tools import mouse_safety, mouse_win32
from desktop_ai_agent.input.ui_elements import (
    UiElement,
    format_for_model,
    match_element,
)

AREAS = ("active", "taskbar", "desktop", "all")
BUTTONS = mouse_safety.BUTTONS
MAX_AGE_SECONDS = 60.0
MAX_LISTED = 60
MIN_CLICK_GAP = mouse_safety.MIN_CLICK_GAP
MAX_CLICKS_PER_MINUTE = mouse_safety.MAX_CLICKS_PER_MINUTE
CORNER_PX = mouse_safety.CORNER_PX
CORNER_LOCK_SECONDS = mouse_safety.CORNER_LOCK_SECONDS
TEXT_TYPES = ("Edit", "ComboBox", "Document")
MAX_TEXT = mouse_safety.MAX_TEXT

_DESKTOP_CLASSES = ("Progman", "WorkerW")
_RISKY = re.compile(
    r"\b(delete|remove|uninstall|send|pay|buy|purchase|format|erase|"
    r"sign\s*out|log\s*out|shut\s*down|restart)\b",
    re.IGNORECASE,
)


@dataclass
class _LastList:
    elements: dict[int, UiElement] = field(default_factory=dict)
    taken_at: float = 0.0


_last: _LastList | None = None
_cleared_by: str | None = None

# Aliases so existing tests can patch ``ui_control._set_cursor`` etc.
_now = mouse_safety.now
_monitors = mouse_safety.monitors
_cursor_pos = mouse_win32.cursor_pos
_set_cursor = mouse_win32.set_cursor
_click = mouse_win32.click
_paste = mouse_win32.paste
in_corner = mouse_safety.in_corner


def reset() -> None:
    """Forget the remembered list, click history and emergency lock."""
    global _last, _cleared_by
    _last = None
    _cleared_by = None
    mouse_safety.reset()


def clear_list_after_action(reason: str) -> None:
    """Used by pointer_control after absolute click/type/drag-down."""
    global _last, _cleared_by
    _last, _cleared_by = None, reason


def filter_area(
    elements: list[UiElement], area: str, foreground: tuple[int, str] | None
) -> list[UiElement]:
    """Keep the elements of one area. ``foreground`` = (hwnd, class) in front."""
    if area == "all":
        return list(elements)
    if area in ("taskbar", "desktop"):
        return [e for e in elements if e.app == area]
    if foreground is None:
        return []
    hwnd, cls = foreground
    if cls in _DESKTOP_CLASSES:
        return [e for e in elements if e.app == "desktop"]
    return [e for e in elements if e.hwnd == hwnd]


def risky_word(name: str) -> str | None:
    match = _RISKY.search(name or "")
    return match.group(0) if match else None


def _bounds(monitors: list[Any]) -> tuple[int, int, int, int] | None:
    if not monitors:
        return None
    return (
        min(m.left for m in monitors),
        min(m.top for m in monitors),
        max(m.right for m in monitors),
        max(m.bottom for m in monitors),
    )


def _on_a_monitor(x: int, y: int, monitors: list[Any]) -> bool:
    return any(m.left <= x < m.right and m.top <= y < m.bottom for m in monitors)


def list_elements(area: str = "active") -> dict[str, Any]:
    """Scan once, keep ``area``, remember the list and return it as numbered text."""
    global _last, _cleared_by
    if area not in AREAS:
        raise ValueError(f"area must be one of {list(AREAS)}, got '{area}'")
    monitors = _monitors()
    elements = _scan(_bounds(monitors))
    picked = filter_area(elements, area, _foreground() if area == "active" else None)
    text, listed = format_for_model(picked, max_items=MAX_LISTED, screen=_bounds(monitors))

    shown = [e for e in picked if e.name][:listed]
    _last, _cleared_by = _LastList({e.id: e for e in shown}, _now()), None

    result: dict[str, Any] = {"area": area, "count": listed, "elements": text}
    if area == "active":
        result["window"] = next((e.app for e in picked if e.app), None)
    if not listed:
        result["note"] = (
            f"No named clickable elements in area '{area}'. Try another area "
            "(taskbar, desktop, all) or take a screenshot."
        )
    elif listed < (named := sum(1 for e in picked if e.name)):
        result["note"] = (
            f"Showing {listed} of {named} elements. If the one you need "
            "is missing, use a narrower area (taskbar, desktop) or take a screenshot."
        )
    return result


def _check_emergency_stop(monitors: list[Any]) -> None:
    """Uses module aliases (``_now``, ``_cursor_pos``) so unit tests can patch them."""
    t = _now()
    if t < mouse_safety._locked_until:
        raise PermissionError(
            f"Mouse control is paused for {mouse_safety._locked_until - t:.0f}s more (emergency stop). "
            "Tell the user, and do not retry."
        )
    if in_corner(*_cursor_pos(), monitors):
        mouse_safety._locked_until = t + CORNER_LOCK_SECONDS
        raise PermissionError(
            "The user pushed the mouse into a screen corner (emergency stop). Mouse control is "
            f"paused for {CORNER_LOCK_SECONDS:.0f}s. Stop and tell the user."
        )


def _resolve(element: int, monitors: list[Any]) -> UiElement:
    if _last is None:
        if _cleared_by:
            raise LookupError(
                f"The element list was cleared by your last {_cleared_by} (the screen may have changed). "
                "Call list_elements, then use the new number."
            )
        raise LookupError("No element list yet. Call list_elements first, then use one of its numbers.")
    age = _now() - _last.taken_at
    if age > MAX_AGE_SECONDS:
        raise LookupError(
            f"The element list is {age:.0f}s old (limit {MAX_AGE_SECONDS:.0f}s). "
            "Call list_elements again first."
        )
    old = _last.elements.get(element)
    if old is None:
        raise LookupError(f"Element {element} is not in the latest list. Use a number from that list.")
    fresh = match_element(old, _scan(_bounds(monitors)))
    if fresh is None:
        raise LookupError(
            f"Element {element} ('{old.name}') has moved, closed or is now hidden. "
            "Call list_elements again."
        )
    x, y = fresh.center
    if not _on_a_monitor(x, y, monitors):
        raise ValueError(f"Element {element} ('{old.name}') is not on any monitor ({x}, {y}).")
    return fresh


def _check_rate() -> float:
    """Uses ``_now`` so unit tests can advance time without touching mouse_safety.now."""
    t = _now()
    times = mouse_safety._click_times
    times[:] = [c for c in times if t - c < 60.0]
    if times and t - times[-1] < MIN_CLICK_GAP:
        raise PermissionError(f"Too fast: wait at least {MIN_CLICK_GAP:g}s between clicks.")
    if len(times) >= MAX_CLICKS_PER_MINUTE:
        raise PermissionError(
            f"Click limit reached ({MAX_CLICKS_PER_MINUTE} per minute). Stop and tell the user."
        )
    return t


def move_mouse(element: int) -> dict[str, Any]:
    """Put the pointer on the centre of an element from the latest list. Never clicks."""
    monitors = _monitors()
    _check_emergency_stop(monitors)
    target = _resolve(element, monitors)
    x, y = target.center
    _set_cursor(x, y)
    return {"moved_to": target.name, "type": target.type, "window": target.app, "x": x, "y": y}


def click(element: int, button: str = "left", double: bool = False) -> dict[str, Any]:
    """Move onto an element from the latest list and click it once (or double-click)."""
    global _last, _cleared_by
    if button not in BUTTONS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    monitors = _monitors()
    _check_emergency_stop(monitors)
    now = _check_rate()
    target = _resolve(element, monitors)
    word = risky_word(target.name)
    if word:
        raise PermissionError(
            f"'{target.name}' looks risky ('{word}'). Do not click it: ask the user to do this themselves."
        )
    x, y = target.center
    _set_cursor(x, y)
    _click(button, 2 if double else 1)
    mouse_safety.record_click(now)
    _last, _cleared_by = None, "click"
    return {
        "clicked": target.name,
        "button": button,
        "double": double,
        "type": target.type,
        "window": target.app,
        "x": x,
        "y": y,
        "note": "The element list is now cleared. Call list_elements again before the next mouse action.",
    }


def type_text(element: int, text: str) -> dict[str, Any]:
    """Click into an input field from the latest list and paste ``text`` there."""
    global _last, _cleared_by
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is too long ({len(text)} > {MAX_TEXT} characters)")
    monitors = _monitors()
    _check_emergency_stop(monitors)
    now = _check_rate()
    target = _resolve(element, monitors)
    if target.type not in TEXT_TYPES:
        raise ValueError(
            f"Element {element} ('{target.name}') is a {target.type}, not an input field. "
            "Pick an Edit, ComboBox or Document element from the list."
        )
    if target.password:
        raise PermissionError("That is a password field. Never type passwords: ask the user to do it.")
    x, y = target.center
    _set_cursor(x, y)
    _click("left", 1)
    _paste(text)
    mouse_safety.record_click(now)
    _last, _cleared_by = None, "typing"
    return {
        "typed_into": target.name,
        "type": target.type,
        "window": target.app,
        "characters": len(text),
        "note": "Text pasted; Enter was not pressed. Call list_elements again before the next mouse action.",
    }


def _scan(bounds: tuple[int, int, int, int] | None) -> list[UiElement]:  # pragma: no cover
    from desktop_ai_agent.input.ui_elements import list_ui_elements

    return list_ui_elements(bounds=bounds)


def _foreground() -> tuple[int, str] | None:  # pragma: no cover
    import os

    from desktop_ai_agent.input.ui_elements import foreground_window

    return foreground_window(exclude_pid=os.getpid())
