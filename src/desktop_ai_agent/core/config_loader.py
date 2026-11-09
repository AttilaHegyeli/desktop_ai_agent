from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from desktop_ai_agent.paths import ENV_FILE, SECRETS_JSON, SETUP_JSON

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
_dotenv_loaded = False


def load_setup(path: Any = None) -> dict[str, Any]:
    target = path or SETUP_JSON
    with open(target, encoding="utf-8") as fh:
        return json.load(fh)


def save_setup(data: dict[str, Any], path: Any = None) -> None:
    target = Path(path or SETUP_JSON)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def load_dotenv(path: Any = None, *, override: bool = False) -> dict[str, str]:
    """Load KEY=VALUE pairs from a .env file into os.environ (no extra dependency)."""
    global _dotenv_loaded
    target = Path(path or ENV_FILE)
    loaded: dict[str, str] = {}
    if not target.is_file():
        _dotenv_loaded = True
        return loaded
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        loaded[key] = value
        if override or key not in os.environ or not str(os.environ.get(key, "")).strip():
            os.environ[key] = value
    _dotenv_loaded = True
    return loaded


def save_openrouter_key_to_env(api_key: str, path: Any = None) -> None:
    """Write/update OPENROUTER_API_KEY in .env (create file if missing)."""
    target = Path(path or ENV_FILE)
    key_name = "OPENROUTER_API_KEY"
    value = (api_key or "").strip()
    lines: list[str] = []
    if target.is_file():
        lines = target.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        m = _ENV_LINE.match(line)
        if m and m.group(1) == key_name:
            found = True
            if value:
                out.append(f"{key_name}={value}")
            # omit line when clearing the key
            continue
        out.append(line)
    if value and not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key_name}={value}")
    text = "\n".join(out).rstrip() + ("\n" if out else "")
    target.write_text(text, encoding="utf-8")
    if value:
        os.environ[key_name] = value
    else:
        os.environ.pop(key_name, None)


def load_secrets(path: Any = None) -> dict[str, Any]:
    target = Path(path or SECRETS_JSON)
    if not target.is_file():
        return {}
    with open(target, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def save_secrets(data: dict[str, Any], path: Any = None) -> None:
    target = Path(path or SECRETS_JSON)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def openrouter_api_key(*, secrets: dict[str, Any] | None = None) -> str | None:
    """Prefer process env, then .env file, then config/secrets.json."""
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv()
    env = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if env:
        return env
    data = secrets if secrets is not None else load_secrets()
    key = data.get("openrouter_api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def response_schema_example(setup: dict[str, Any] | None = None) -> dict[str, Any]:
    data = setup or load_setup()
    return deepcopy(data.get("formats", {}).get("example", {}))
