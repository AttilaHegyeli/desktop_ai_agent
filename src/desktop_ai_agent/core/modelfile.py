from __future__ import annotations

import re
from pathlib import Path


_UNSUPPORTED_PARAM = re.compile(
    r"^\s*PARAMETER\s+(think|format)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def load_modelfile(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_optional_parameters(text: str) -> str:
    """Remove think/format lines for older Ollama builds; /set covers them at runtime."""
    cleaned = _UNSUPPORTED_PARAM.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"
