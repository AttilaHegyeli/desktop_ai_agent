"""List the open windows and focus / minimize / maximize / restore / close one (LLM path).

The model names a window ("spotify") or passes a number from the latest list. The
matching is ours, not the model's:
  - exactly one window matches -> act on it
  - several or none            -> change nothing; the error lists the numbered windows
                                  (and the closest name) so the model picks a number
Numbers are checked again before acting: same window handle, same program.

close sends WM_CLOSE, like the window's X button: the app may ask "Save changes?".
Nothing is ever saved or discarded for the user; the result says the window is still
open and the model asks the user.

The agent's own browser also shows its TABS, listed under its window and numbered in
the same list. A tab can be focused (switched to) or closed by name or number; a name
that matches both a tab and its window means the tab. minimize / maximize / restore on
a tab act on its browser window. A page that asks "Leave site?" keeps its tab open.
"""
from __future__ import annotations

import difflib
import os
import time
from dataclasses import dataclass, field
from typing import Any

from desktop_ai_agent.action.tools import mouse_safety, page_control
from desktop_ai_agent.action.tools.page_control import TabInfo
from desktop_ai_agent.input import windows as _windows
from desktop_ai_agent.input.element_ranking import words, words_match
from desktop_ai_agent.input.windows import WindowInfo

ACTIONS = ("focus", "minimize", "maximize", "restore", "close")
MAX_LISTED = 30
MAX_AGE_SECONDS = 120.0
CLOSE_WAIT_SECONDS = 2.0
_POLL_SECONDS = 0.1
_CLOSEST_CUTOFF = 0.5

_SHOW = {"minimize": _windows.SW_MINIMIZE, "maximize": _windows.SW_MAXIMIZE, "restore": _windows.SW_RESTORE}


@dataclass
class _LastList:
    windows: dict[int, WindowInfo | TabInfo] = field(default_factory=dict)  # number -> window or tab
    taken_at: float = 0.0


Entry = WindowInfo | TabInfo


_last: _LastList | None = None

# Thin OS seams, patched by the tests (no real window is ever touched there).
_now = time.monotonic
_sleep = time.sleep
_show = _windows.show
_state = _windows.window_state
_bring_to_front = _windows.bring_to_front
_request_close = _windows.request_close


def _enumerate() -> list[WindowInfo]:  # pragma: no cover - needs Windows
    return _windows.list_open_windows(own_pid=os.getpid())


def _tabs() -> list[TabInfo]:  # pragma: no cover - needs the agent's browser
    return page_control.tabs()


def _tab_problem() -> str:
    return page_control.tab_problem()


def reset() -> None:
    global _last
    _last = None


# ---- pure helpers ------------------------------------------------------------------

def _tab_line(number: int, tab: TabInfo) -> str:
    flags = [f for f in (tab.program, "current" if tab.active else "") if f]
    title = tab.title if len(tab.title) <= 70 else tab.title[:67] + "..."
    return f'  {number}: tab "{title}"' + (f" ({', '.join(flags)})" if flags else "")


def _line(number: int, win: Entry) -> str:
    if isinstance(win, TabInfo):
        return _tab_line(number, win)
    flags = [win.program] if win.program else []
    if win.minimized:
        flags.append("minimized")
    elif win.maximized:
        flags.append("maximized")
    title = win.title if len(win.title) <= 70 else win.title[:67] + "..."
    return f'{number}: "{title}"' + (f" ({', '.join(flags)})" if flags else "")


def with_tabs(wins: list[WindowInfo], tabs: list[TabInfo]) -> list[Entry]:
    """Windows in order, each agent-browser window followed by its tabs. Tabs whose
    window is unknown go last, so they are never lost."""
    out: list[Entry] = []
    placed: set[int] = set()
    for win in wins:
        out.append(win)
        mine = [tab for tab in tabs if tab.hwnd and tab.hwnd == win.hwnd]
        out.extend(mine)
        placed.update(id(tab) for tab in mine)
    out.extend(tab for tab in tabs if id(tab) not in placed)
    return out


def format_windows(numbered: dict[int, Entry]) -> str:
    return "\n".join(_line(n, w) for n, w in numbered.items())


def _window_words(win: Entry) -> frozenset[str]:
    return words(win.title) | words(win.program)


def matches(query: str, wins: list[Entry]) -> list[Entry]:
    """Windows `query` refers to. An exact title or program name wins outright; otherwise
    every query word must match a word of the title or program (song/songs, edge/msedge)."""
    q = " ".join(query.casefold().split())
    if not q:
        return []
    exact = [w for w in wins if q in (w.title.casefold(), w.program.casefold())]
    if exact:
        return exact
    asked = words(q)

    def hit(word: str, win: Entry) -> bool:
        return any(words_match(word, own) for own in _window_words(win)) or (
            len(word) >= 3 and word in win.program.casefold()
        )

    return [w for w in wins if asked and all(hit(word, w) for word in asked)]


def closest(query: str, numbered: dict[int, Entry]) -> int | None:
    """Number of the window whose title or program looks most like `query`, if any is close."""
    q = query.casefold()
    best: tuple[float, int] | None = None
    for number, win in numbered.items():
        texts = [t for t in (win.title, win.program) if t]
        ratio = max((difflib.SequenceMatcher(None, q, t.casefold()).ratio() for t in texts), default=0.0)
        if ratio >= _CLOSEST_CUTOFF and (best is None or ratio > best[0]):
            best = (ratio, number)
    return best[1] if best else None


# ---- the two tools -----------------------------------------------------------------

def _remember(wins: list[Entry]) -> dict[int, Entry]:
    global _last
    numbered = {i: w for i, w in enumerate(wins[:MAX_LISTED], start=1)}
    _last = _LastList(numbered, _now())
    return numbered


def list_windows() -> dict[str, Any]:
    """Numbered open windows, front first. Remembered so window_control can use the numbers."""
    wins = with_tabs(_enumerate(), _tabs())
    numbered = _remember(wins)
    result: dict[str, Any] = {"count": len(numbered), "windows": format_windows(numbered)}
    notes = []
    if len(wins) > len(numbered):
        notes.append(f"Showing {len(numbered)} of {len(wins)} windows (front first).")
    elif not numbered:
        notes.append("No open windows.")
    problem = _tab_problem()
    if problem:
        notes.append(f"The agent browser's tabs could not be read ({problem}).")
    if notes:
        result["note"] = " ".join(notes)
    return result


def _same(old: Entry, new: Entry) -> bool:
    if isinstance(old, TabInfo):
        return isinstance(new, TabInfo) and new.key == old.key
    return isinstance(new, WindowInfo) and new.hwnd == old.hwnd and new.program == old.program


def _by_number(number: int, fresh: list[Entry]) -> Entry:
    if _last is None:
        raise LookupError("No window list yet. Call list_windows, or pass the window's name instead.")
    age = _now() - _last.taken_at
    if age > MAX_AGE_SECONDS:
        raise LookupError(f"The window list is {age:.0f}s old. Call list_windows again, or pass the name.")
    old = _last.windows.get(number)
    if old is None:
        raise LookupError(f"Window {number} is not in the latest list. Use a number from that list.")
    now = next((w for w in fresh if _same(old, w)), None)
    if now is None:
        raise LookupError(f"Window {number} ('{old.title}') is no longer open. Call list_windows again.")
    return now  # the fresh copy: the title and state may have changed since


def _by_name(name: str, fresh: list[Entry]) -> Entry:
    found = matches(name, fresh)
    if any(isinstance(f, TabInfo) for f in found):
        found = [f for f in found if isinstance(f, TabInfo)]  # "youtube" = the tab, not its window
    if len(found) == 1:
        return found[0]
    # Ambiguous or unknown: change nothing, and give numbers to choose from.
    numbered = _remember(found if found else fresh)
    listing = format_windows(numbered)
    if found:
        raise LookupError(
            f"Several windows match '{name}':\n{listing}\n"
            "Nothing was changed. Call window_control again with the number of the one the user means, "
            "or ask the user if it is unclear."
        )
    hint = closest(name, numbered)
    near = f" Closest: {_line(hint, numbered[hint])}." if hint else ""
    raise LookupError(
        f"No open window matches '{name}'.{near} Open windows:\n{listing or '(none)'}\n"
        "Nothing was changed. Use a number from this list, or open the app with open_app."
    )


def _pick(name: str | None, number: int | None) -> Entry:
    if (name is None) == (number is None):
        raise ValueError("Pass exactly one of name or number.")
    fresh = with_tabs(_enumerate(), _tabs())
    return _by_number(number, fresh) if number is not None else _by_name(str(name), fresh)


def _wait_until_gone(hwnd: int) -> bool:
    deadline = _now() + CLOSE_WAIT_SECONDS
    while True:
        if not _state(hwnd)[0]:
            return True
        if _now() >= deadline:
            return False
        _sleep(_POLL_SECONDS)


def window_control(action: str, name: str | None = None, number: int | None = None) -> dict[str, Any]:
    """Do `action` to one open window, picked by name or by number from the latest list."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {list(ACTIONS)}, got '{action}'")
    mouse_safety.check_lock_only()  # the emergency stop pauses every on-screen action
    win = _pick(name, number)
    _clear_element_list()  # the screen changes: element numbers from before are stale
    if isinstance(win, TabInfo):
        if action == "focus":
            return {**page_control.focus_tab(win), "action": action}
        if action == "close":
            return {**page_control.close_tab(win), "action": action}
        if not win.hwnd:
            raise LookupError(f"The browser window of tab '{win.title}' was not found. Call list_windows again.")
        win = next((w for w in _enumerate() if w.hwnd == win.hwnd), None) or win
        if isinstance(win, TabInfo):
            raise LookupError("The agent's browser window is not open any more. Call list_windows again.")
    result: dict[str, Any] = {"window": win.title, "program": win.program, "action": action}

    if action == "focus":
        result["in_front"] = _bring_to_front(win.hwnd)
        if not result["in_front"]:
            result["note"] = "Windows refused to bring it to the front. Tell the user to click it once."
        return result

    if action == "close":
        _request_close(win.hwnd)
        result["closed"] = _wait_until_gone(win.hwnd)
        if not result["closed"]:
            result["note"] = (
                "The window is still open: the app is probably asking something, such as whether to "
                "save changes. Do not choose for the user: ask them what to do."
            )
        return result

    _show(win.hwnd, _SHOW[action])
    alive, minimized, maximized = _state(win.hwnd)
    result["state"] = ("minimized" if minimized else "maximized" if maximized else "normal") if alive else "closed"
    return result


def _clear_element_list() -> None:
    from desktop_ai_agent.action.tools import ui_control

    ui_control.clear_list_after_action("window_control")
