"""Windows-safe subprocess text I/O (avoid cp1252 UnicodeDecodeError)."""

from __future__ import annotations

import subprocess
from typing import Any


ENCODING = "utf-8"
ERRORS = "replace"


def run_text(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", ENCODING)
    kwargs.setdefault("errors", ERRORS)
    return subprocess.run(args, **kwargs)


def popen_text(args: list[str], **kwargs: Any) -> subprocess.Popen[str]:
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", ENCODING)
    kwargs.setdefault("errors", ERRORS)
    return subprocess.Popen(args, **kwargs)
