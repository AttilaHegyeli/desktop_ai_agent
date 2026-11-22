"""Block *physical* keyboard/mouse input while a phone call is active.

Uses Windows low-level hooks (WH_KEYBOARD_LL / WH_MOUSE_LL) that swallow events
that are not flagged as injected. The agent's own actions (SendInput, hand-gesture
mouse, UI Automation typing) are injected, so they keep working. No admin needed.

Failsafes: Ctrl+Alt+Del always works (secure desktop); holding Esc for
``escape_hold_s`` calls ``on_escape``; the hook is removed on ``release()`` and
at interpreter exit.
"""
from __future__ import annotations

import atexit
import sys
import threading
import time
from typing import Callable, Protocol

VK_ESCAPE = 0x1B


class HookBackend(Protocol):
    def start(self, decide: Callable[[str, bool, int, bool], bool]) -> None:
        """Install hooks. ``decide(kind, injected, vk, down)`` -> True to swallow."""

    def stop(self) -> None: ...


class InputLock:
    def __init__(
        self,
        backend: HookBackend | None = None,
        *,
        on_escape: Callable[[], None] | None = None,
        escape_hold_s: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._on_escape = on_escape
        self._escape_hold_s = escape_hold_s
        self._clock = clock
        self._engaged = False
        self._esc_since: float | None = None
        self._esc_fired = False
        self._lock = threading.Lock()
        atexit.register(self.release)

    @property
    def is_engaged(self) -> bool:
        return self._engaged

    def engage(self) -> bool:
        with self._lock:
            if self._engaged:
                return True
            if self._backend is None:
                self._backend = default_backend()
            if self._backend is None:  # non-Windows
                return False
            self._esc_since = None
            self._esc_fired = False
            self._backend.start(self.decide)
            self._engaged = True
            return True

    def release(self) -> None:
        with self._lock:
            if not self._engaged:
                return
            self._engaged = False
            try:
                if self._backend is not None:
                    self._backend.stop()
            except Exception:  # noqa: BLE001 - release must never raise
                pass

    def decide(self, kind: str, injected: bool, vk: int, down: bool) -> bool:
        """Return True to swallow the event. Runs inside the hook callback."""
        if injected:
            return False
        if kind == "key" and vk == VK_ESCAPE:
            self._track_escape(down)
        return self._engaged

    def _track_escape(self, down: bool) -> None:
        if not down:
            self._esc_since = None
            self._esc_fired = False
            return
        now = self._clock()
        if self._esc_since is None:
            self._esc_since = now
            return
        if not self._esc_fired and now - self._esc_since >= self._escape_hold_s:
            self._esc_fired = True
            cb = self._on_escape
            if cb is not None:
                threading.Thread(target=cb, daemon=True, name="input-lock-escape").start()


def default_backend() -> HookBackend | None:
    if sys.platform != "win32":
        return None
    return Win32HookBackend()


class Win32HookBackend:
    """ctypes low-level hooks on a dedicated message-pump thread."""

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_QUIT = 0x0012
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    KEY_INJECTED = 0x10 | 0x02  # LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED
    MOUSE_INJECTED = 0x01 | 0x02  # LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Exception | None = None

    def start(self, decide: Callable[[str, bool, int, bool], bool]) -> None:
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, args=(decide,), daemon=True, name="input-lock-hooks"
        )
        self._thread.start()
        self._ready.wait(2.0)
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        import ctypes

        if self._thread is not None and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                ctypes.c_ulong(self._thread_id), self.WM_QUIT, 0, 0
            )
            self._thread.join(2.0)
        self._thread = None

    def _run(self, decide: Callable[[str, bool, int, bool], bool]) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        # Without these, 64-bit handles are truncated to 32 bits and the hook fails.
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.restype = ctypes.c_int
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]

        def key_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code >= 0:
                try:
                    info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    injected = bool(info.flags & self.KEY_INJECTED)
                    down = w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                    if decide("key", injected, int(info.vkCode), down):
                        return 1
                except Exception:  # noqa: BLE001 - never block input on a bug
                    pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code >= 0:
                try:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    injected = bool(info.flags & self.MOUSE_INJECTED)
                    if decide("mouse", injected, 0, True):
                        return 1
                except Exception:  # noqa: BLE001
                    pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        # Keep references alive for the hook lifetime.
        kb_cb = HOOKPROC(key_proc)
        ms_cb = HOOKPROC(mouse_proc)
        hmod = kernel32.GetModuleHandleW(None)
        self._thread_id = kernel32.GetCurrentThreadId()
        kb = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, kb_cb, hmod, 0)
        ms = user32.SetWindowsHookExW(self.WH_MOUSE_LL, ms_cb, hmod, 0)
        if not kb or not ms:
            self._error = RuntimeError(
                f"Could not install input hooks (Win32 error {ctypes.get_last_error()})"
            )
            for h in (kb, ms):
                if h:
                    user32.UnhookWindowsHookEx(h)
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(kb)
            user32.UnhookWindowsHookEx(ms)
