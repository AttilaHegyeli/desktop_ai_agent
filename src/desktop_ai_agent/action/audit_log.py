"""Audit log: one JSON line per event (rejected, declined, execute, result).

Writes to a JSONL file if a path is given; always keeps recent entries in memory
(handy for tests and for showing recent activity in the UI).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: str | Path | None = None, max_field_chars: int = 300) -> None:
        self.path = Path(path) if path else None
        self.max_field_chars = max_field_chars
        self.entries: deque[dict[str, Any]] = deque(maxlen=1000)
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _clip(self, value: Any) -> Any:
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if len(text) > self.max_field_chars:
            return text[: self.max_field_chars] + "...[truncated]"
        return value

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        entry.update({k: self._clip(v) for k, v in fields.items()})
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
        return entry
