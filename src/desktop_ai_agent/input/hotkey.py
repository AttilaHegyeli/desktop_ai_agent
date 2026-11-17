"""Global hold-to-talk key (default CTRL) detected by polling GetAsyncKeyState.

Polled from the Tk loop (no hook thread, no extra deps). To avoid firing on
shortcuts such as Ctrl+C, the key must be held alone for ``hold_ms`` first; if
any other key is pressed meanwhile the hold is discarded until release.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable

_VK_CONTROL = 0x11
# Keys that do not count as "another key" (mouse buttons, modifiers).
_IGNORED = frozenset(
    {0x01, 0x02, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
)
_CHECK_KEYS = tuple(vk for vk in range(0x08, 0xE0) if vk not in _IGNORED)


def _down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)  # type: ignore[attr-defined]


def ctrl_down() -> bool:
    return sys.platform == "win32" and _down(_VK_CONTROL)


def other_key_down() -> bool:
    return any(_down(vk) for vk in _CHECK_KEYS)


class HoldKey:
    """State machine: idle -> pending -> active -> idle.

    ``poll()`` returns "press" once when a clean hold is confirmed, "release"
    when an active hold ends, else None. Inputs are injectable for tests.
    """

    def __init__(
        self,
        *,
        hold_ms: int = 250,
        key_down: Callable[[], bool] = ctrl_down,
        other_down: Callable[[], bool] = other_key_down,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hold_s = max(0, hold_ms) / 1000.0
        self._key_down = key_down
        self._other_down = other_down
        self._clock = clock
        self._since: float | None = None
        self._active = False
        self._tainted = False

    @property
    def active(self) -> bool:
        return self._active

    def poll(self) -> str | None:
        down = self._key_down()
        if not down:
            was_active = self._active
            self._since = None
            self._active = False
            self._tainted = False
            return "release" if was_active else None
        if self._active:
            return None
        if self._since is None:
            self._since = self._clock()
            self._tainted = False
        if self._tainted:
            return None
        if self._other_down():
            self._tainted = True
            return None
        if self._clock() - self._since >= self.hold_s:
            self._active = True
            return "press"
        return None
