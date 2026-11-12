"""Parse voice/typed shortcuts: type/write, enter, right-click at cursor."""
from __future__ import annotations

import re
from dataclasses import dataclass

_TYPE_VERBS = ("type", "write")
_TRAILING = re.compile(r"\s+(here|there)\s*$", re.IGNORECASE)

_ENTER_PHRASES = frozenset(
    {
        "enter",
        "press enter",
        "hit enter",
        "press return",
        "hit return",
        "return",
    }
)
_RIGHT_CLICK_PHRASES = frozenset(
    {
        "click",
        "right click",
        "rightclick",
        "click here",
        "click there",
        "right click here",
        "right click there",
    }
)


@dataclass(frozen=True)
class TypeCommand:
    text: str


@dataclass(frozen=True)
class EnterCommand:
    pass


@dataclass(frozen=True)
class RightClickCommand:
    pass


def normalize_command(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_type_command(text: str) -> TypeCommand | None:
    """Return a TypeCommand if ``text`` is ``type/write <payload> [here|there]``."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Keep original payload casing from the source after the verb.
    lower = raw.lower()
    verb = None
    verb_len = 0
    for candidate in _TYPE_VERBS:
        prefix = candidate + " "
        if lower.startswith(prefix):
            verb = candidate
            verb_len = len(prefix)
            break
    if verb is None:
        return None
    payload = raw[verb_len:].strip()
    if normalize_command(payload) in ("", "here", "there"):
        return None
    payload = _TRAILING.sub("", payload).strip()
    if not payload or normalize_command(payload) in ("here", "there"):
        return None
    return TypeCommand(text=payload)


def parse_enter_command(text: str) -> EnterCommand | None:
    """Return EnterCommand for ``enter`` / ``press enter`` (and close variants)."""
    if normalize_command(text) in _ENTER_PHRASES:
        return EnterCommand()
    return None


def parse_right_click_command(text: str) -> RightClickCommand | None:
    """Return RightClickCommand for bare ``click`` / ``right click`` at the cursor."""
    if normalize_command(text) in _RIGHT_CLICK_PHRASES:
        return RightClickCommand()
    return None
