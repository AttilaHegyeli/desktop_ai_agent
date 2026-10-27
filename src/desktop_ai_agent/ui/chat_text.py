"""Normalize agent replies for the compact overlay chat (pure helpers, no tkinter)."""
from __future__ import annotations

import json
import re

# Soft ceiling so a runaway dump cannot freeze the overlay Text widget.
MAX_CHAT_CHARS = 4_000


def normalize_chat_reply(text: str | None) -> str:
    """Make a reply readable and safe for the overlay chat panel.

    - ``provide_script`` JSON → labeled script body (scrollable, not one huge line)
    - Collapse runaway whitespace
    - Soft-truncate extreme dumps (full text still lands in the terminal log)
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    if raw.startswith("{") and ('"code"' in raw or "'code'" in raw):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and "code" in obj:
            script = str(obj.get("script") or "").strip() or "script"
            code = str(obj.get("code") or "").strip()
            body = f"Script ready: {script}"
            if code:
                body = f"{body}\n\n{code}"
            return _soft_clip(body)

    cleaned = re.sub(r"[ \t]+\n", "\n", raw)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return _soft_clip(cleaned.strip())


def _soft_clip(text: str) -> str:
    if len(text) <= MAX_CHAT_CHARS:
        return text
    return text[: MAX_CHAT_CHARS - 1].rstrip() + "…"
