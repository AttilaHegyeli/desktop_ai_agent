"""Absolute screen-pointer actions for hand / sensors (NOT registered for the LLM)."""
from __future__ import annotations

from typing import Any

from desktop_ai_agent.action.tools import mouse_safety, mouse_win32

BUTTONS = mouse_safety.BUTTONS
MAX_TEXT = mouse_safety.MAX_TEXT


def _clear_elements(reason: str) -> None:
    from desktop_ai_agent.action.tools import ui_control

    ui_control.clear_list_after_action(reason)


def move_cursor_at(x: int, y: int) -> dict[str, Any]:
    """Move the pointer to absolute screen pixels. No click; no rate limit.

    Does not arm the corner emergency lock (hand follow reaches screen edges).
    """
    mouse_safety.check_emergency_stop(arm_corner=False)
    xi, yi = int(x), int(y)
    mouse_win32.set_cursor(xi, yi)
    return {"moved_to": "absolute", "x": xi, "y": yi}


def click_at(
    x: int, y: int, button: str = "left", double: bool = False
) -> dict[str, Any]:
    """Left/right click at absolute screen pixels."""
    if button not in BUTTONS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    mouse_safety.check_emergency_stop()
    at = mouse_safety.check_rate()
    xi, yi = int(x), int(y)
    mouse_win32.set_cursor(xi, yi)
    mouse_win32.click(button, 2 if double else 1)
    mouse_safety.record_click(at)
    _clear_elements("click")
    return {
        "clicked": "absolute",
        "button": button,
        "double": double,
        "x": xi,
        "y": yi,
        "note": "The element list is now cleared. Call list_elements again before the next mouse action.",
    }


def mouse_down_at(x: int, y: int, button: str = "left") -> dict[str, Any]:
    """Press and hold a mouse button at absolute pixels (start of a drag).

    No click rate limit — hand drag must not fail after a recent click.
    """
    if button not in BUTTONS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    mouse_safety.check_emergency_stop(arm_corner=False)
    xi, yi = int(x), int(y)
    mouse_win32.set_cursor(xi, yi)
    mouse_win32.button_down(button)
    _clear_elements("click")
    return {"down": "absolute", "button": button, "x": xi, "y": yi}


def mouse_up_at(x: int, y: int, button: str = "left") -> dict[str, Any]:
    """Release a mouse button at absolute pixels (end of a drag).

    Always releases the button even if emergency-stop would block other moves,
    so the OS never gets stuck with the button held down.
    """
    if button not in BUTTONS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    xi, yi = int(x), int(y)
    try:
        mouse_win32.set_cursor(xi, yi)
    except OSError:
        pass
    mouse_win32.button_up(button)
    return {"up": "absolute", "button": button, "x": xi, "y": yi}


def scroll_at(x: int, y: int, dy: int) -> dict[str, Any]:
    """Move to ``(x, y)`` and scroll the wheel. Positive ``dy`` scrolls up."""
    mouse_safety.check_emergency_stop(arm_corner=False)
    xi, yi = int(x), int(y)
    mouse_win32.set_cursor(xi, yi)
    ticks = int(dy)
    if ticks:
        mouse_win32.scroll_wheel(ticks)
    return {"scrolled": "absolute", "x": xi, "y": yi, "dy": ticks}


def paste_text(text: str) -> dict[str, Any]:
    """Paste ``text`` at the current keyboard focus (no click, no pointer move)."""
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is too long ({len(text)} > {MAX_TEXT} characters)")
    mouse_safety.check_lock_only()
    mouse_win32.paste(text)
    _clear_elements("typing")
    return {
        "typed": "focus",
        "characters": len(text),
        "note": "Text pasted at current focus; Enter was not pressed.",
    }


def press_enter() -> dict[str, Any]:
    """Press the Enter key at the current keyboard focus."""
    mouse_safety.check_lock_only()
    mouse_win32.press_enter()
    return {"pressed": "enter"}


def right_click_here() -> dict[str, Any]:
    """Right-click at the current cursor position (no move)."""
    mouse_safety.check_emergency_stop()
    at = mouse_safety.check_rate()
    xi, yi = mouse_win32.cursor_pos()
    mouse_win32.click("right", 1)
    mouse_safety.record_click(at)
    _clear_elements("click")
    return {
        "clicked": "right",
        "x": xi,
        "y": yi,
        "note": "The element list is now cleared. Call list_elements again before the next mouse action.",
    }


def type_at(x: int, y: int, text: str) -> dict[str, Any]:
    """Click at absolute pixels and paste ``text`` (no Enter)."""
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is too long ({len(text)} > {MAX_TEXT} characters)")
    mouse_safety.check_emergency_stop()
    at = mouse_safety.check_rate()
    xi, yi = int(x), int(y)
    mouse_win32.set_cursor(xi, yi)
    mouse_win32.click("left", 1)
    mouse_win32.paste(text)
    mouse_safety.record_click(at)
    _clear_elements("typing")
    return {
        "typed_at": "absolute",
        "x": xi,
        "y": yi,
        "characters": len(text),
        "note": "Text pasted; Enter was not pressed. Call list_elements again before the next mouse action.",
    }
