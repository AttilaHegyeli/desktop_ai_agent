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
    return ""