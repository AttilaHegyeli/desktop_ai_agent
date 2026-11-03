"""Turn agent events into terminal text. Pure functions: easy to test, no tkinter."""
from __future__ import annotations

from typing import Any

MAX_OUTPUT_CHARS = 200


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "..."


def _format_step(step: Any) -> str:
    if not isinstance(step, dict):
        return _clip(step)
    args = step.get("arguments")
    if isinstance(args, dict):
        inner = ", ".join(f"{k}={v!r}" for k, v in args.items())
    else:
        inner = _clip(args) if args else ""
    return f"{step.get('name')}({inner})"


def format_event(kind: str, data: Any) -> str:
    """Return one line (with trailing newline) for the terminal, or '' to skip."""
    if kind == "plan":
        return "[plan] " + " -> ".join(_format_step(s) for s in data) + "\n"
    if kind == "result":
        if getattr(data, "ok", False):
            return f"[ok] {data.name} -> {_clip(data.output)}\n"
        return f"[{data.status}] {data.name}: {_clip(data.error)}\n"
    if kind == "final":
        return f"{data}\n"
    if kind == "screenshot":
        return f"[screenshot] {data}\n"
    if kind == "nudge":
        return f"[nudge] model announced an action without doing it: {_clip(data)}\n"
    if kind == "follow_up":
        return "[auto] " + " -> ".join(_format_step(s) for s in data) + "\n"
    if kind == "stuck":
        return f"[stuck] model repeated a failing call: {_clip(', '.join(data))}\n"
    if kind == "recovered":
        return f"[recovered] model wrote a tool call as text: {_clip(data)}\n"
    if kind == "handoff":
        return f"[handoff] context almost full: task continues in a fresh chat (#{data})\n"
    return ""