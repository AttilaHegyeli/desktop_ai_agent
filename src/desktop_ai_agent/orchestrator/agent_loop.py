"""The agent loop: user request -> model -> plan -> executor -> results -> model.

The orchestrator never touches the OS. It only talks to the model (core) and
hands plans to the executor (action). All safety lives in the executor.

Stop conditions (so a confused model can't loop forever and burn tokens):
  - model answers without tool calls            -> "done"
  - an ends_turn action (provide_script) succeeds -> "done", text = its output as JSON
  - user declines a high-risk step              -> "declined"
  - too many consecutive failed rounds          -> "failed"
  - too many rounds overall                     -> "max_rounds"
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from desktop_ai_agent.action.executor import Executor, StepResult, plan_from_tool_calls

EventFn = Callable[[str, Any], None]

# English phrasing that usually means "look at my screen". Used only to decide
# whether to auto-capture; the screenshot tool still works for any other case.
_VISION_RE = re.compile(
    r"""
    \b(
        screenshot | screen\s*shot |
        # "what do you see" / "what you see" / "tell me what you see" / "can you see"
        (tell\s+me\s+|can\s+you\s+|do\s+you\s+)?what\s+(do\s+you\s+|you\s+)?see |
        (can|do)\s+you\s+see |
        what\s+(is|'s)\s+(there|here) |
        what\s+(is|'s)\s+on\s+(the\s+|my\s+)?(screen|desktop|monitor|display|window) |
        what('s|\s+is)\s+playing |
        now[\s-]?playing |
        (tell\s+me\s+)?(the\s+)?(first|current|selected)\s+(song|track|tab|window|app) |
        which\s+(song|track|tab|window|app|file) |
        (look|looking)\s+(at\s+)?(my\s+)?(screen|desktop|monitor|display) |
        (on|at)\s+(my\s+)?(screen|desktop|monitor|display|computer) |
        (describe|read|check)\s+(the\s+|my\s+|this\s+)?(screen|desktop|window|app) |
        (read|check)\s+(this|that|it) |
        current\s+(screen|desktop|window) |
        (show|see)\s+(me\s+)?(my\s+)?(screen|desktop|monitor|display) |
        visible\s+on\s+(my\s+|the\s+)?(screen|desktop|monitor|display)
    )\b
    |
    \b(see|seeing)\s+(on|what).{0,40}\b(screen|desktop|monitor|computer|display)\b
    |
    # "open Spotify and tell me what is there" / "launch X and describe what you see"
    \b(open|launch)\b.{0,100}\b(
        tell\s+me\s+what |
        what\s+(you\s+see|is\s+there|'s\s+there|is\s+playing) |
        describe\s+(what|it|the) |
        (first|current)\s+(song|track)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Actions that can change what is on screen; mid-task vision refresh only after these.
_UI_CHANGING = frozenset({"wait", "open_app", "web_open"})


def needs_screen_vision(text: str) -> bool:
    """True when the user message likely requires seeing the desktop."""
    return bool(text and _VISION_RE.search(text))


@dataclass
class TaskResult:
    status: str  # "done" | "declined" | "failed" | "max_rounds"
    text: str
    rounds: int


def _skipped_message(name: str) -> str:
    return json.dumps(
        {
            "action": name,
            "status": "skipped",
            "error": "Not executed because the plan was rejected or an earlier step did not succeed.",
        }
    )


def _png_b64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _drop_prior_images(client: Any) -> None:
    """Drop stale screenshots from chat history before attaching a fresh one."""
    drop = getattr(client, "drop_images", None)
    if callable(drop):
        drop()


def _attach_screenshot_images(client: Any, results: list[StepResult]) -> None:
    """After an explicit screenshot tool call, feed the PNG as a multimodal user message.

    Tool results stay text-only (path/size). Ollama vision expects images on user messages.
    Prior images are dropped so the model only sees the new capture.
    """
    fresh: list[str] = []
    for r in results:
        if not r.ok or r.name != "screenshot" or not isinstance(r.output, dict):
            continue
        path = r.output.get("path")
        if not path:
            continue
        try:
            fresh.append(_png_b64(path))
        except OSError:
            continue
    if not fresh:
        return
    _drop_prior_images(client)
    for b64 in fresh:
        client.add_user("Screenshot attached.", images=[b64])


def _capture_b64(executor: Executor, emit: EventFn) -> str | None:
    """Run the screenshot action and return base64 PNG, or None on failure."""
    if "screenshot" not in executor.registry:
        return None
    results = executor.run([{"name": "screenshot", "arguments": {}}])
    if not results:
        return None
    r = results[0]
    emit("result", r)
    if not r.ok or not isinstance(r.output, dict):
        return None
    path = r.output.get("path")
    if not path:
        return None
    try:
        return _png_b64(path)
    except OSError:
        return None


def run_task(
    client: Any,  # core.chat_client.ChatClient (anything with add_user/chat/add_tool_result)
    executor: Executor,
    user_text: str,
    *,
    images: list[str] | None = None,
    auto_screenshot: bool = True,
    max_rounds: int = 8,
    max_failed_rounds: int = 2,
    on_event: EventFn | None = None,
) -> TaskResult:
    emit: EventFn = on_event or (lambda kind, data: None)
    attach = list(images) if images else []
    # Auto-capture only when this request needs screen vision (not on every hello/script).
    vision = bool(attach) or (auto_screenshot and needs_screen_vision(user_text))
    if vision and auto_screenshot and not attach:
        b64 = _capture_b64(executor, emit)
        if b64:
            attach = [b64]
            emit("screenshot", "auto")
        else:
            vision = False
    # New vision input: drop stale screenshots from prior turns, then attach fresh ones.
    if attach:
        _drop_prior_images(client)
    client.add_user(user_text, attach or None)
    tools = executor.registry.to_tools()  # what the model sees == what we enforce
    failed_rounds = 0
    last_errors = ""

    for round_no in range(1, max_rounds + 1):
        turn = client.chat(tools=tools)
        if not turn.tool_calls:
            emit("final", turn.content)
            return TaskResult("done", turn.content, round_no)

        plan = plan_from_tool_calls(turn.tool_calls)
        emit("plan", plan)
        results: list[StepResult] = executor.run(plan)
        by_index = {r.index: r for r in results}

        # Every tool call must get a tool message back, run or not.
        for i, step in enumerate(plan):
            name = str(step.get("name"))
            r = by_index.get(i)
            client.add_tool_result(name, r.to_message() if r else _skipped_message(name))
            if r:
                emit("result", r)

        had_screenshot = any(r.ok and r.name == "screenshot" for r in results)
        if had_screenshot:
            vision = True
        _attach_screenshot_images(client, results)

        if any(r.status == "declined" for r in results):
            return TaskResult("declined", "A step needed your approval and was not approved.", round_no)

        if len(results) == len(plan) and all(r.ok for r in results):
            # An action flagged ends_turn (e.g. provide_script) IS the final reply:
            # stop here instead of asking the model to comment on it again.
            finals = [r for r in results if executor.registry.get(r.name).ends_turn]
            if finals:
                text = json.dumps(finals[-1].output, ensure_ascii=False)  # compact, one line
                emit("final", text)
                return TaskResult("done", text, round_no)
            failed_rounds = 0
            # Mid-task refresh only while this task is already using vision, the
            # plan did not just capture, and a UI-changing action ran.
            ui_changed = any(r.ok and r.name in _UI_CHANGING for r in results)
            if auto_screenshot and vision and not had_screenshot and ui_changed:
                b64 = _capture_b64(executor, emit)
                if b64:
                    _drop_prior_images(client)
                    client.add_user("Current screen.", images=[b64])
                    emit("screenshot", "auto")
            continue

        failed_rounds += 1
        last_errors = "; ".join(r.error or r.status for r in results if not r.ok)
        if failed_rounds > max_failed_rounds:
            return TaskResult("failed", f"Gave up after repeated failures: {last_errors}", round_no)

    return TaskResult("max_rounds", f"Stopped after {max_rounds} rounds.", max_rounds)