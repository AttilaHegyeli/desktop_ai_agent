from __future__ import annotations

import json

from desktop_ai_agent.core.response_format import (
    AGENT_JSON_SCHEMA,
    agent_system_prompt,
    normalize_agent_reply,
    title_from_user,
)


def test_agent_prompt_is_windows_scriptwriter() -> None:
    prompt = agent_system_prompt()
    assert "Action Layer" in prompt
    assert "Windows" in prompt
    assert "No comments" in prompt
    assert "resolve_path" in prompt


def test_json_schema_requires_script_and_code() -> None:
    assert AGENT_JSON_SCHEMA["required"] == ["script", "code"]


def test_title_from_user_aliases() -> None:
    assert title_from_user("run vscode") == "open_vs_code"
    assert title_from_user("run youtube") == "open_youtube"
    assert title_from_user("get marosvasarhely weather") == "get_marosvasarhely_weather"
    assert title_from_user("weather Budapest") == "get_budapest_weather"
    assert title_from_user("open steam") == "open_steam"
    assert title_from_user("") == "run_task"


def test_normalize_replaces_short_script() -> None:
    raw = '{"script":"short","code":"import webbrowser\\nwebbrowser.open(\'https://www.youtube.com\')"}'
    out = json.loads(normalize_agent_reply(raw, user_text="run youtube"))
    assert out["script"] == "open_youtube"
    assert "webbrowser" in out["code"]
    assert out["code"].endswith("\n")


def test_normalize_keeps_good_script() -> None:
    raw = '{"script":"open_steam","code":"print(1)"}'
    out = json.loads(normalize_agent_reply(raw, user_text="open steam"))
    assert out["script"] == "open_steam"
    assert out["code"] == "print(1)\n"


def test_normalize_strips_markdown_fence() -> None:
    raw = '```json\n{"script":"list_desktop","code":"print(2)"}\n```'
    out = json.loads(normalize_agent_reply(raw, user_text="list desktop"))
    assert out["script"] == "list_desktop"
    assert out["code"] == "print(2)\n"


def test_normalize_extracts_json_from_spam_prefix() -> None:
    raw = '< ,,Short,, code:> {"script":"open_vs_code","code":"print(1)"}'
    out = json.loads(normalize_agent_reply(raw, user_text="run vscode"))
    assert out["script"] == "open_vs_code"


def test_normalize_empty_uses_fallback_title() -> None:
    out = json.loads(normalize_agent_reply("", user_text="run vscode"))
    assert out["script"] == "open_vs_code"
    assert out["code"] == "pass\n"


def test_normalize_garbage_uses_fallback() -> None:
    out = json.loads(normalize_agent_reply("not json at all", user_text="open steam"))
    assert out["script"] == "open_steam"
    assert out["code"] == "pass\n"
