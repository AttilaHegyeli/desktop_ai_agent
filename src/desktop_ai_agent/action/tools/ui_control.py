"""Point at and click on-screen elements found through UI Automation.

Flow the model follows:
  1. list_elements(area)   -> numbered list of visible clickable elements (remembered here)
  2. move_mouse(element)   -> rescan, make sure that element is still the same one in the
     or click(element)        same place, then put the pointer on its centre (and click)

The model only ever passes an area name, an element number and a mouse button.
Coordinates come from Windows, never from the model (AGENTS.md rule 6).

Safety for clicks (the executor logs every step; these rules live here because they
depend on the live screen):
  - after a click the element list is cleared, so the model must look again
  - at least MIN_CLICK_GAP seconds between clicks, at most MAX_CLICKS_PER_MINUTE
  - emergency stop: pointer pushed into a screen corner -> no mouse actions for
    CORNER_LOCK_SECONDS
  - elements with risky names (Delete, Send, Pay, ...) are refused; the user does those
  - type_text only targets input fields, never password fields, and never presses Enter

The thin helpers at the bottom are the only parts that touch Windows, so tests
replace them and the real mouse never moves or clicks.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from desktop_ai_agent.input.ui_elements import (
    UiElement,
    format_for_model,
    match_element,
)

AREAS = ("active", "taskbar", "desktop", "all")
BUTTONS = ("left", "right")
MAX_AGE_SECONDS = 60.0  # an element list older than this must be refreshed before acting
MAX_LISTED = 60  # keeps the reply small enough for the model's context
MIN_CLICK_GAP = 1.0
MAX_CLICKS_PER_MINUTE = 10
CORNER_PX = 5  # how close to a monitor corner counts as "pushed into the corner"
CORNER_LOCK_SECONDS = 30.0
TEXT_TYPES = ("Edit", "ComboBox", "Document")  # elements type_text may write into
MAX_TEXT = 2000

_DESKTOP_CLASSES = ("Progman", "WorkerW")
_RISKY = re.compile(
    r"\b(delete|remove|uninstall|send|pay|buy|purchase|format|erase|"
    r"sign\s*out|log\s*out|shut\s*down|restart)\b",
    re.IGNORECASE,
)


@dataclass
class _LastList:
    elements: dict[int, UiElement] = field(default_factory=dict)  # id -> element, listed ones only
    taken_at: float = 0.0


_last: _LastList | None = None
_cleared_by: str | None = None  # 'click' / 'typing' when an action cleared the list
_click_times: list[float] = []
_locked_until = 0.0


def reset() -> None:
    """Forget the remembered list, click history and emergency lock (tests; a new chat)."""
    global _last, _locked_until, _cleared_by
    _last = None
    _cleared_by = None
    _click_times.clear()
    _locked_until = 0.0


# ---- pure helpers -----------------------------------------------------------------

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


def risky_word(name: str) -> str | None:
    """The risky word in an element's name ('Delete', 'Send', ...), or None."""
    match = _RISKY.search(name or "")
    return match.group(0) if match else None


def in_corner(x: int, y: int, monitors: list[Any], margin: int = CORNER_PX) -> bool:
    """Is (x, y) within `margin` px of a corner of any monitor?"""
    for m in monitors:
        near_x = x - m.left <= margin or (m.right - 1) - x <= margin
        near_y = y - m.top <= margin or (m.bottom - 1) - y <= margin
        inside = m.left <= x < m.right and m.top <= y < m.bottom
        if inside and near_x and near_y:
            return True
    return False


def _bounds(monitors: list[Any]) -> tuple[int, int, int, int] | None:
    if not monitors:
        return None
    return (min(m.left for m in monitors), min(m.top for m in monitors),
            max(m.right for m in monitors), max(m.bottom for m in monitors))


def _on_a_monitor(x: int, y: int, monitors: list[Any]) -> bool:
    return any(m.left <= x < m.right and m.top <= y < m.bottom for m in monitors)


# ---- actions ------------------------------------------------------------------------

def list_elements(area: str = "active") -> dict[str, Any]:
    """Scan once, keep `area`, remember the list and return it as numbered text."""
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
        # Tell the model which window it is looking at, so it notices a wrong one in front.
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
    """Refuse every mouse action while the corner lock is on, or if the user just
    pushed the pointer into a corner (that starts the lock)."""
    global _locked_until
    now = _now()
    if now < _locked_until:
        raise PermissionError(
            f"Mouse control is paused for {_locked_until - now:.0f}s more (emergency stop). "
            "Tell the user, and do not retry."
        )
    if in_corner(*_cursor_pos(), monitors):
        _locked_until = now + CORNER_LOCK_SECONDS
        raise PermissionError(
            "The user pushed the mouse into a screen corner (emergency stop). Mouse control is "
            f"paused for {CORNER_LOCK_SECONDS:.0f}s. Stop and tell the user."
        )


def _resolve(element: int, monitors: list[Any]) -> UiElement:
    """The listed element, found again in a fresh scan and on a monitor; or a clear error."""
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
    """Enforce the click rate limits (typing counts as a click). Returns the time now."""
    now = _now()
    _click_times[:] = [t for t in _click_times if now - t < 60.0]
    if _click_times and now - _click_times[-1] < MIN_CLICK_GAP:
        raise PermissionError(f"Too fast: wait at least {MIN_CLICK_GAP:g}s between clicks.")
    if len(_click_times) >= MAX_CLICKS_PER_MINUTE:
        raise PermissionError(
            f"Click limit reached ({MAX_CLICKS_PER_MINUTE} per minute). Stop and tell the user."
        )
    return now


def move_mouse(element: int) -> dict[str, Any]:
    """Put the pointer on the centre of an element from the latest list. Never clicks."""
    monitors = _monitors()
    _check_emergency_stop(monitors)
    target = _resolve(element, monitors)
    x, y = target.center
    _set_cursor(x, y)
    return {"moved_to": target.name, "type": target.type, "window": target.app, "x": x, "y": y}


def click(element: int, button: str = "left", double: bool = False) -> dict[str, Any]:
    """Move onto an element from the latest list and click it once (or double-click it).

    A double-click counts as one click for the rate limit."""
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
    _click_times.append(now)
    _last, _cleared_by = None, "click"  # the screen may have changed: look again first
    return {
        "clicked": target.name, "button": button, "double": double, "type": target.type, "window": target.app,
        "x": x, "y": y,
        "note": "The element list is now cleared. Call list_elements again before the next mouse action.",
    }


def type_text(element: int, text: str) -> dict[str, Any]:
    """Click into an input field from the latest list and paste `text` there.

    Pasting (clipboard + Ctrl+V) works in nearly every app and with any characters.
    The user's text clipboard is put back afterwards. Enter is never pressed.
    """
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
    _click("left", 1)  # focus the field
    _paste(text)
    _click_times.append(now)
    _last, _cleared_by = None, "typing"
    return {
        "typed_into": target.name, "type": target.type, "window": target.app, "characters": len(text),
        "note": "Text pasted; Enter was not pressed. Call list_elements again before the next mouse action.",
    }


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


def _user32() -> Any:  # pragma: no cover
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


def _cursor_pos() -> tuple[int, int]:  # pragma: no cover - reads the real pointer
    import ctypes
    from ctypes import wintypes

    point = wintypes.POINT()
    if not _user32().GetCursorPos(ctypes.byref(point)):
        raise OSError(f"GetCursorPos failed (error {ctypes.get_last_error()})")
    return point.x, point.y


def _set_cursor(x: int, y: int) -> None:  # pragma: no cover - moves the real pointer
    import ctypes

    if not _user32().SetCursorPos(int(x), int(y)):
        raise OSError(f"SetCursorPos({x}, {y}) failed (error {ctypes.get_last_error()})")


_MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}  # (down, up)


def _click(button: str, count: int = 1) -> None:  # pragma: no cover - clicks the real mouse
    """`count` clicks at the current pointer position (down + up each), back to back so
    Windows sees a double-click."""
    user32 = _user32()
    down, up = _MOUSE_FLAGS[button]
    for _ in range(count):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)


# Clipboard + Ctrl+V. Signatures are declared so 64-bit handles are not truncated.
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_VK_CONTROL, _VK_V, _KEYEVENTF_KEYUP = 0x11, 0x56, 0x0002


def _clipboard_api() -> tuple[Any, Any]:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = _user32(), ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return user32, kernel32


def _open_clipboard(user32: Any) -> None:  # pragma: no cover - needs Windows
    for _ in range(20):  # another app may hold it for a moment
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise OSError("Could not open the clipboard (another program is using it)")


def _get_clipboard_text() -> str | None:  # pragma: no cover - reads the real clipboard
    import ctypes

    user32, kernel32 = _clipboard_api()
    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:  # pragma: no cover - writes the real clipboard
    import ctypes

    user32, kernel32 = _clipboard_api()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not handle:
        raise OSError("GlobalAlloc failed")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, size)
    kernel32.GlobalUnlock(handle)
    _open_clipboard(user32)
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)  # ownership passes to Windows only on success
            raise OSError(f"SetClipboardData failed (error {ctypes.get_last_error()})")
    finally:
        user32.CloseClipboard()


def _paste(text: str) -> None:  # pragma: no cover - uses the real clipboard and keyboard
    """Put `text` on the clipboard, press Ctrl+V, then restore the user's text clipboard."""
    saved = _get_clipboard_text()
    _set_clipboard_text(text)
    user32 = _user32()
    time.sleep(0.05)  # let the click's focus settle
    user32.keybd_event(_VK_CONTROL, 0, 0, 0)
    user32.keybd_event(_VK_V, 0, 0, 0)
    user32.keybd_event(_VK_V, 0, _KEYEVENTF_KEYUP, 0)
    user32.keybd_event(_VK_CONTROL, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)  # apps read the clipboard after the key press; restore only afterwards
    if saved is not None:
        _set_clipboard_text(saved)
