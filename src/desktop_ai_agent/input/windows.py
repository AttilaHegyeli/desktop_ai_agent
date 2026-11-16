"""The open top-level windows, and the few Win32 calls that change them.

"Open window" means what the user sees in Alt+Tab: visible (minimized counts), titled,
not a tool window, not owned by another window (dialogs belong to their app), not
cloaked (Windows hides some windows that way, e.g. on other virtual desktops), not a
shell window, and not one of ours. That filter is the pure `parse_windows`, tested with
fake rows. Everything Win32 sits in thin functions at the bottom (stdlib ctypes only).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any

# The desktop and the taskbar are top-level windows too, but never "an app".
_SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    program: str  # executable name without .exe, e.g. "Spotify"; "" if Windows refused to say
    pid: int
    minimized: bool = False
    maximized: bool = False


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def program_name(exe_path: str) -> str:
    """'C:\\...\\Spotify.exe' -> 'Spotify'."""
    return PureWindowsPath(exe_path).stem if exe_path else ""


def parse_windows(rows: list[Any], own_pid: int | None = None) -> list[WindowInfo]:
    """Keep the rows that are open user windows, in z-order (front first).

    A row is {"hwnd", "title", "cls", "pid", "exe", "visible", "minimized", "maximized",
    "tool", "owned", "cloaked"}. Malformed rows are dropped.
    """
    found: list[WindowInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        hwnd, pid = row.get("hwnd"), row.get("pid")
        if not isinstance(hwnd, int) or isinstance(hwnd, bool) or not hwnd or not isinstance(pid, int):
            continue
        title = _clean(row.get("title"))
        if (
            not title
            or not row.get("visible")
            or row.get("tool")
            or row.get("owned")
            or row.get("cloaked")
            or row.get("cls") in _SHELL_CLASSES
            or (own_pid is not None and pid == own_pid)
        ):
            continue
        found.append(WindowInfo(hwnd, title, program_name(str(row.get("exe") or "")), pid,
                                minimized=bool(row.get("minimized")), maximized=bool(row.get("maximized"))))
    return found


def list_open_windows(own_pid: int | None = None) -> list[WindowInfo]:  # pragma: no cover - needs Windows
    return parse_windows(_enum_rows(), own_pid)


# ---- Windows (thin, not unit tested) ----------------------------------------------

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_WM_CLOSE = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_VK_MENU, _KEYEVENTF_KEYUP = 0x12, 0x0002

_api: Any = None


def _win() -> Any:  # pragma: no cover - needs Windows
    """Own DLL handles with explicit signatures (never the shared ctypes.windll ones)."""
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes as w

        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        d = ctypes.WinDLL("dwmapi")
        proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        u.EnumWindows.argtypes = [proc, w.LPARAM]
        for name in ("IsWindowVisible", "IsIconic", "IsZoomed", "IsWindow", "SetForegroundWindow",
                     "GetWindowTextLengthW"):
            getattr(u, name).argtypes = [w.HWND]
        u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.GetWindow.argtypes = [w.HWND, w.UINT]
        u.GetWindow.restype = w.HWND
        u.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = w.LONG
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.GetForegroundWindow.restype = w.HWND
        u.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.keybd_event.argtypes = [w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t]
        u.keybd_event.restype = None
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        k.CloseHandle.argtypes = [w.HANDLE]
        d.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        d.DwmGetWindowAttribute.restype = ctypes.c_long
        _api = (ctypes, w, u, k, d, proc)
    return _api


def _exe_path(pid: int) -> str:  # pragma: no cover - needs Windows
    ctypes, w, _u, k, _d, _p = _win()
    handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""  # e.g. an elevated process
    try:
        size = w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(handle)


def _enum_rows() -> list[dict[str, Any]]:  # pragma: no cover - needs Windows
    ctypes, w, u, _k, d, proc = _win()
    rows: list[dict[str, Any]] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if not hwnd or not u.IsWindowVisible(hwnd):
            return True  # hidden windows are never listed: skip the expensive calls
        length = u.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cloaked = w.DWORD()
        d.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        rows.append({
            "hwnd": int(hwnd), "title": title.value, "cls": cls.value, "pid": int(pid.value),
            "exe": _exe_path(int(pid.value)), "visible": True,
            "minimized": bool(u.IsIconic(hwnd)), "maximized": bool(u.IsZoomed(hwnd)),
            "tool": bool(u.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW),
            "owned": bool(u.GetWindow(hwnd, _GW_OWNER)), "cloaked": bool(cloaked.value),
        })
        return True

    u.EnumWindows(proc(visit), 0)  # top-level windows in z-order, front first
    return rows


def z_order() -> list[int]:  # pragma: no cover - needs Windows
    """Handles of all visible top-level windows, front first (taskbar and desktop included)."""
    _ctypes, _w, u, _k, _d, proc = _win()
    found: list[int] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if hwnd and u.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    u.EnumWindows(proc(visit), 0)
    return found


def show(hwnd: int, command: int) -> None:  # pragma: no cover - needs Windows
    _win()[2].ShowWindow(hwnd, command)


def window_state(hwnd: int) -> tuple[bool, bool, bool]:  # pragma: no cover - needs Windows
    """(still on screen, minimized, maximized). Hidden counts as gone (apps that close to the tray)."""
    u = _win()[2]
    alive = bool(u.IsWindow(hwnd)) and bool(u.IsWindowVisible(hwnd))
    return alive, alive and bool(u.IsIconic(hwnd)), alive and bool(u.IsZoomed(hwnd))


def bring_to_front(hwnd: int) -> bool:  # pragma: no cover - needs Windows
    """Restore if minimized and make it the foreground window. True if it really is in front.

    Windows only lets the foreground process hand over the foreground. When our chat bar
    is in front that is us; otherwise a lone Alt key press (no other key) lifts the lock,
    the trick AutoHotkey and others use. It types nothing.
    """
    u = _win()[2]
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    elif u.GetForegroundWindow() == hwnd:
        return True  # already in front (e.g. an app that just opened): touch nothing
    if u.SetForegroundWindow(hwnd) and u.GetForegroundWindow() == hwnd:
        return True
    u.keybd_event(_VK_MENU, 0, 0, 0)
    u.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    u.SetForegroundWindow(hwnd)
    return u.GetForegroundWindow() == hwnd


def request_close(hwnd: int) -> None:  # pragma: no cover - needs Windows
    """WM_CLOSE: the same message the window's X button sends, so the app may still ask to save."""
    _win()[2].PostMessageW(hwnd, _WM_CLOSE, 0, 0)
