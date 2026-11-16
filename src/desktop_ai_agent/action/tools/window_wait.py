"""Make open_app / web_open end with the app's window on screen, not just "launched".

Without this, the screenshot or element list taken right after open_app shows the
screen from BEFORE the app appeared (the Spotify-shows-VS-Code bug). So:

  open_app  - already running?  -> bring its window to the front, launch nothing
            - else launch, then wait (up to TIMEOUT_SECONDS) for a NEW window of that app
  web_open  - open the URL, then wait for a new window or a changed window title
              (a new tab in an open browser changes that window's title)

Which windows belong to an app:
  - by program: the window's executable equals the launched one (Spotify.exe)
  - by title, only for windows that appeared after the launch: some apps draw their
    window from a helper process (Steam -> steamwebhelper.exe). Title matching is
    never used to decide "already running": a browser tab titled "Claude Code docs"
    must not count as VS Code.
On timeout the action still succeeds, with window None and a note for the model.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from desktop_ai_agent.action.tools.window_control import matches
from desktop_ai_agent.input import windows as _windows
from desktop_ai_agent.input.windows import WindowInfo

TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.25

NO_WINDOW_NOTE = (
    "No window appeared within {s:g}s. It may still be loading or run only in the "
    "background: wait, then take a screenshot or call list_windows."
)

# Thin OS seams, patched by the tests.
_now = time.monotonic
_sleep = time.sleep
_bring_to_front = _windows.bring_to_front


def _enumerate() -> list[WindowInfo]:  # pragma: no cover - needs Windows
    try:
        return _windows.list_open_windows(own_pid=os.getpid())
    except OSError:
        return []  # waiting is a convenience: never let it break the launch


def by_program(wins: list[WindowInfo], exe_stem: str) -> list[WindowInfo]:
    stem = exe_stem.casefold()
    return [w for w in wins if stem and w.program.casefold() == stem]


def by_title(wins: list[WindowInfo], app_name: str) -> list[WindowInfo]:
    return matches(app_name.replace("_", " "), wins)  # index names use _ for spaces


def _poll(found: Callable[[list[WindowInfo]], WindowInfo | None]) -> tuple[WindowInfo | None, float]:
    start = _now()
    while True:
        win = found(_enumerate())
        waited = _now() - start
        if win is not None or waited >= TIMEOUT_SECONDS:
            return win, waited
        _sleep(POLL_SECONDS)


def _finish(result: dict[str, Any], win: WindowInfo | None, waited: float) -> dict[str, Any]:
    result["waited_seconds"] = round(waited, 1)
    if win is None:
        result["window"] = None
        result["note"] = NO_WINDOW_NOTE.format(s=TIMEOUT_SECONDS)
    else:
        result["window"] = win.title
        result["in_front"] = _bring_to_front(win.hwnd)
    return result


def open_app_window(app_name: str, exe_stem: str, launch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Focus the app if it already has a window, else `launch()` it and wait for its window.

    `exe_stem` is the indexed executable's name without .exe ("" for Store apps, which
    run inside a shared host process; those are recognised by title instead).
    """
    before = _enumerate()
    running = by_program(before, exe_stem) if exe_stem else by_title(before, app_name)
    if running:
        win = running[0]  # the front-most one
        return {"opened": app_name, "already_running": True, "window": win.title,
                "in_front": _bring_to_front(win.hwnd)}

    result = dict(launch())
    old = {w.hwnd for w in before}

    def new_app_window(wins: list[WindowInfo]) -> WindowInfo | None:
        fresh = [w for w in wins if w.hwnd not in old]
        hits = by_program(fresh, exe_stem) or by_title(fresh, app_name)
        return hits[0] if hits else None

    return _finish(result, *_poll(new_app_window))


def open_page_window(open_page: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """`open_page()`, then wait for a new window or a window whose title changed."""
    before = {w.hwnd: w.title for w in _enumerate()}
    result = dict(open_page())

    def changed(wins: list[WindowInfo]) -> WindowInfo | None:
        return next((w for w in wins if before.get(w.hwnd) != w.title), None)

    return _finish(result, *_poll(changed))
