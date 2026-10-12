from __future__ import annotations

import json
import re
from typing import Any

AGENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "script": {"type": "string"},
        "code": {"type": "string"},
    },
    "required": ["script", "code"],
}

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_BAD_SCRIPT = frozenset(
    {"", "short", "task", "empty", "script", "code", "intent", "label", "title", "none", "null"}
)

_AGENT_PROMPT = """You are the Core scriptwriter for a desktop Action Layer.
Consumer is a program, not a human. Target OS is always Windows.

INPUT: one task string (later: orchestrator JSON).
OUTPUT: only this JSON object:
{"script":"<snake_case_job_id>","code":"<python_source>"}

script = machine job id from the task (open_vs_code, open_youtube, get_marosvasarhely_weather).
code   = the only field Action will exec. Must run unchanged.

Procedure:
1) Parse the task.
2) If an installed app/path is needed, prefer:
   from desktop_ai_agent.core.folder_index import resolve_path
   path = resolve_path("name")
3) Emit the smallest working Windows Python for the task.
4) Any stdlib / project tool is allowed.
5) No try/except, no raise, no secrets, no placeholders, no markdown, no prose.
6) No comments and no notes in code or outside JSON. Windows-only APIs/paths (os.startfile, cmd, PowerShell when needed).
"""


def agent_system_prompt() -> str:
    return _AGENT_PROMPT


def title_from_user(user_text: str) -> str:
    raw = (user_text or "").strip().lower()
    if not raw:
        return "run_task"

    for src, dst in (
        ("visual studio code", "vs_code"),
        ("vs code", "vs_code"),
        ("vscode", "vs_code"),
        ("marosvásárhely", "marosvasarhely"),
        ("tirgu mures", "marosvasarhely"),
        ("targu mures", "marosvasarhely"),
        ("yt", "youtube"),
    ):
        raw = raw.replace(src, dst)

    tokens = [t for t in _SLUG_RE.split(raw) if t]
    if not tokens:
        return "run_task"

    verbs = {
        "run": "open",
        "launch": "open",
        "start": "open",
        "open": "open",
        "get": "get",
        "show": "get",
        "fetch": "get",
        "check": "get",
        "list": "list",
        "print": "print",
        "search": "search",
        "find": "find",
        "close": "close",
    }
    verb = verbs.get(tokens[0], "")
    rest = tokens[1:] if verb else tokens
    if not verb:
        if "weather" in tokens:
            verb = "get"
        elif any(t in tokens for t in ("youtube", "steam", "vs_code", "chrome", "spotify")):
            verb = "open"
        else:
            verb = "run"

    if rest and rest[0] == verb:
        rest = rest[1:]

    if verb == "get" and "weather" in rest:
        place = [t for t in rest if t != "weather"]
        parts = [verb, *place, "weather"] if place else [verb, "weather"]
    else:
        parts = [verb, *rest] if rest else [verb, "task"]

    return _SLUG_RE.sub("_", "_".join(parts)).strip("_")[:80] or "run_task"


def normalize_agent_reply(raw: str, user_text: str | None = None) -> str:
    fallback = title_from_user(user_text or "")
    text = (raw or "").strip()
    if not text:
        return _pack(fallback, "pass\n")

    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    parsed = _parse(text)
    if parsed is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            parsed = _parse(text[start : end + 1])

    if parsed is None:
        return _pack(fallback, "pass\n")

    script = _fix_script(str(parsed.get("script", "")), fallback)
    code = str(parsed.get("code", "pass")).strip() or "pass"
    if not code.endswith("\n"):
        code += "\n"
    return _pack(script, code)


def _fix_script(script: str, fallback: str) -> str:
    slug = _SLUG_RE.sub("_", script.strip().lower()).strip("_")
    if slug in _BAD_SCRIPT or len(slug) < 3:
        return fallback
    return slug[:80]


def _pack(script: str, code: str) -> str:
    return json.dumps({"script": script, "code": code}, ensure_ascii=False)


def _parse(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and ("script" in data or "code" in data):
        return data
    return None
