"""Thin Windows mouse / clipboard primitives (replaced by mocks in tests)."""
from __future__ import annotations

import time
from typing import Any

BUTTONS = ("left", "right")
_MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}  # (down, up)
_MOUSEEVENTF_WHEEL = 0x0800
_WHEEL_DELTA = 120

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_VK_CONTROL, _VK_V, _VK_RETURN, _KEYEVENTF_KEYUP = 0x11, 0x56, 0x0D, 0x0002


def user32() -> Any:  # pragma: no cover
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


def cursor_pos() -> tuple[int, int]:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    point = wintypes.POINT()
    if not user32().GetCursorPos(ctypes.byref(point)):
        raise OSError(f"GetCursorPos failed (error {ctypes.get_last_error()})")
    return point.x, point.y


def set_cursor(x: int, y: int) -> None:  # pragma: no cover
    import ctypes

    if not user32().SetCursorPos(int(x), int(y)):
        raise OSError(f"SetCursorPos({x}, {y}) failed (error {ctypes.get_last_error()})")


def button_down(button: str = "left") -> None:  # pragma: no cover
    if button not in _MOUSE_FLAGS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    down, _ = _MOUSE_FLAGS[button]
    user32().mouse_event(down, 0, 0, 0, 0)


def button_up(button: str = "left") -> None:  # pragma: no cover
    if button not in _MOUSE_FLAGS:
        raise ValueError(f"button must be one of {list(BUTTONS)}, got '{button}'")
    _, up = _MOUSE_FLAGS[button]
    user32().mouse_event(up, 0, 0, 0, 0)


def click(button: str, count: int = 1) -> None:  # pragma: no cover
    """``count`` clicks at the current pointer (down+up each)."""
    for _ in range(count):
        button_down(button)
        button_up(button)


def scroll_wheel(dy: int) -> None:  # pragma: no cover
    """Positive ``dy`` scrolls up; negative scrolls down (Windows wheel ticks)."""
    if dy == 0:
        return
    user32().mouse_event(_MOUSEEVENTF_WHEEL, 0, 0, int(dy) * _WHEEL_DELTA, 0)


def _clipboard_api() -> tuple[Any, Any]:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    u32, kernel32 = user32(), ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return u32, kernel32


def _open_clipboard(u32: Any) -> None:  # pragma: no cover
    for _ in range(20):
        if u32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise OSError("Could not open the clipboard (another program is using it)")


def get_clipboard_text() -> str | None:  # pragma: no cover
    import ctypes

    u32, kernel32 = _clipboard_api()
    _open_clipboard(u32)
    try:
        handle = u32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


def set_clipboard_text(text: str) -> None:  # pragma: no cover
    import ctypes

    u32, kernel32 = _clipboard_api()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not handle:
        raise OSError("GlobalAlloc failed")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, size)
    kernel32.GlobalUnlock(handle)
    _open_clipboard(u32)
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(_CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError(f"SetClipboardData failed (error {ctypes.get_last_error()})")
    finally:
        u32.CloseClipboard()


def press_key(vk: int) -> None:  # pragma: no cover
    """Tap a virtual-key code (down + up)."""
    u32 = user32()
    u32.keybd_event(int(vk), 0, 0, 0)
    u32.keybd_event(int(vk), 0, _KEYEVENTF_KEYUP, 0)


def press_enter() -> None:  # pragma: no cover
    """Press the Enter / Return key."""
    press_key(_VK_RETURN)


def paste(text: str) -> None:  # pragma: no cover
    """Put ``text`` on the clipboard, Ctrl+V, then restore the user's text clipboard."""
    saved = get_clipboard_text()
    set_clipboard_text(text)
    u32 = user32()
    time.sleep(0.05)
    u32.keybd_event(_VK_CONTROL, 0, 0, 0)
    u32.keybd_event(_VK_V, 0, 0, 0)
    u32.keybd_event(_VK_V, 0, _KEYEVENTF_KEYUP, 0)
    u32.keybd_event(_VK_CONTROL, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)
    if saved is not None:
        set_clipboard_text(saved)
