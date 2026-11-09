"""The agent loop: user request -> model -> plan -> executor -> results -> model.

The orchestrator never touches the OS. It only talks to the model (core) and
hands plans to the executor (action). All safety lives in the executor.

Stop conditions (so a confused model can't loop forever and burn tokens):
  - model answers without tool calls            -> "done"
  - an ends_turn action (provide_script) succeeds -> "done", text = its output as JSON
  - user declines a high-risk step              -> "declined"
  - too many consecutive failed rounds          -> "failed"
  - too many rounds overall                     -> "max_rounds"
  - the user pressed Stop                        -> "stopped"
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from desktop_ai_agent.action.executor import Executor, StepResult, plan_from_tool_calls
from desktop_ai_agent.core.chat_client import ChatCancelled
from desktop_ai_agent.core.openrouter_client import OpenRouterError
from desktop_ai_agent.orchestrator.task_state import TaskState

EventFn = Callable[[str, Any], None]
StopFn = Callable[[], bool]

STOPPED_TEXT = "Stopped."

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

EMPTY_REPLY = "Done."

# A final message that promises an action instead of calling the tool ("I will now type...").
_PROMISE_RE = re.compile(
    r"\b(i\s+will|i'll|i\s+am\s+going\s+to|i'm\s+going\s+to|next,?\s+i|now\s+i|let\s+me(?!\s+know))\b",
    re.IGNORECASE,
)
# A reply that asks the user or offers an option is waiting for an answer: never push it.
_ASKS_USER_RE = re.compile(
    r"\?\s*$|\b(do\s+you\s+want|would\s+you\s+like|shall\s+i|should\s+i|want\s+me\s+to)\b",
    re.IGNORECASE,
)
NUDGE = (
    "You said you would do something but did not call a tool. Call the tool now only if the "
    "user asked for that action. Do not take actions the user did not ask for. "
    "Otherwise give your final answer."
)


def promises_action(text: str) -> bool:
    """True when a reply announces an action ("I will now...") rather than reporting one,
    asking the user, or offering an option."""
    return bool(text and _PROMISE_RE.search(text) and not _ASKS_USER_RE.search(text))


# Gemma sometimes writes its native tool-call syntax as plain text instead of a real
# tool call, e.g. "call:wait{seconds:5}<tool_call|>". Ollama then returns no tool_calls.
_LEAKED_NAME_RE = re.compile(r"call:([A-Za-z_]\w*)\s*(?=\{)")
_GEMMA_QUOTE = '<|"|>'  # Gemma's string delimiter inside tool-call arguments
_BARE_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_]\w*)\s*:")
_BARE_VALUE_RE = re.compile(r"(:\s*)([A-Za-z_][\w.\-]*)(\s*[,}])")


def _brace_block(text: str, start: int) -> str | None:
    """The {...} block that starts at text[start], respecting quoted strings."""
    depth = 0
    i = start
    while i < len(text):
        if text.startswith(_GEMMA_QUOTE, i):
            end = text.find(_GEMMA_QUOTE, i + len(_GEMMA_QUOTE))
            if end < 0:
                return None
            i = end + len(_GEMMA_QUOTE)
            continue
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def _loose_args(block: str) -> dict[str, Any] | None:
    """Parse JSON or Gemma's loose form ({seconds:5}, {name:<|"|>spotify<|"|>})."""
    try:
        value = json.loads(block)
    except json.JSONDecodeError:
        parts = block.split(_GEMMA_QUOTE)
        if len(parts) % 2 == 0:
            return None  # an unclosed string
        out = []
        for i, part in enumerate(parts):
            if i % 2:
                out.append(json.dumps(part))  # the text between delimiters is a string
                continue
            part = _BARE_KEY_RE.sub(r'\1"\2":', part)
            part = _BARE_VALUE_RE.sub(
                lambda m: m.group(0)
                if m.group(2) in ("true", "false", "null")
                else f'{m.group(1)}"{m.group(2)}"{m.group(3)}',
                part,
            )
            out.append(part)
        try:
            value = json.loads("".join(out))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def leaked_tool_calls(text: str, registry: Any) -> list[dict[str, Any]]:
    """Tool calls written as text, as real tool calls. Only names in the registry count."""
    calls: list[dict[str, Any]] = []
    for m in _LEAKED_NAME_RE.finditer(text or ""):
        name = m.group(1)
        if name not in registry:
            continue
        block = _brace_block(text, m.end())
        args = _loose_args(block) if block else None
        if args is not None:
            calls.append({"function": {"name": name, "arguments": args}})
    return calls


# Actions that can change what is on screen; mid-task vision refresh only after these.
_UI_CHANGING = frozenset({"wait", "open_app", "web_open", "click"})

# Large tool results that go stale: only the newest copy stays in the chat history.
_SUPERSEDED = {
    "list_elements": json.dumps({
        "action": "list_elements",
        "status": "superseded",
        "note": "Older element list removed. Its numbers are no longer valid.",
    }),
}


# Follow-up steps the orchestrator runs itself after a plan succeeds, so the model
# does not spend a whole round asking for them. Keyed by the last UI-changing action.
# Not open_app/web_open: "open X" is often the whole task, so a list would be wasted.
_LIST_ACTIVE = {"name": "list_elements", "arguments": {"area": "active"}}
_FOLLOW_UPS: dict[str, tuple[dict[str, Any], ...]] = {
    "click": (_LIST_ACTIVE,),
    "type_text": (_LIST_ACTIVE,),
}

# Actions after which an earlier element list no longer matches the screen.
_STALES_ELEMENTS = frozenset({"open_app", "web_open", "click", "type_text"})

# When the chat fills this share of the context window, restart it with a handoff note.
HANDOFF_AT = 0.85
MAX_HANDOFFS = 3
_HANDOFF_STEPS = 15  # newest step lines kept in the note
_CHARS_PER_TOKEN = 4  # rough estimate for text added since the last measured reply

STUCK_HINT = (
    "You already tried {calls} and it failed the same way. Do not repeat it. "
    "Try a different approach, or tell the user what is blocking you."
)


def needs_screen_vision(text: str) -> bool:
    """True when the user message likely requires seeing the desktop."""
    return bool(text and _VISION_RE.search(text))


# A request that starts by opening something ("open spotify and tell me what you see").
_OPENS_FIRST_RE = re.compile(
    r"^\W*(?:(?:hey|please|can\s+you|could\s+you|would\s+you)\W+)*(?:open|launch|start)\s+\S",
    re.IGNORECASE,
)


def opens_first(text: str) -> bool:
    """True when the request opens an app or page before anything can be looked at."""
    return bool(text and _OPENS_FIRST_RE.search(text))


@dataclass
class TaskResult:
    status: str  # "done" | "declined" | "failed" | "max_rounds" | "stopped"
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


def _compact_history(client: Any) -> None:
    """Replace older copies of stale, large tool results with a short note."""
    compact = getattr(client, "compact_tool_results", None)
    if callable(compact):
        for name, stub in _SUPERSEDED.items():
            compact(name, stub)


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


def follow_up_plan(plan: list[dict[str, Any]], registry: Any) -> list[dict[str, Any]]:
    """Steps to run automatically after `plan` succeeded, or [] when none are needed."""
    names = [str(step.get("name")) for step in plan]
    last = max((i for i, n in enumerate(names) if n in _FOLLOW_UPS), default=None)
    if last is None or "list_elements" not in registry:
        return []
    if "list_elements" in names[last + 1 :]:
        return []  # the model already listed the elements after the change
    return [
        {"name": s["name"], "arguments": dict(s["arguments"])}
        for s in _FOLLOW_UPS[names[last]]
        if s["name"] in registry
    ]


def _clip(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _signature(step: dict[str, Any]) -> str:
    args = json.dumps(step.get("arguments") or {}, sort_keys=True, ensure_ascii=False, default=str)
    return f"{step.get('name')}({args})"


def _add_note(client: Any, state: TaskState, text: str) -> None:
    """A user message from the orchestrator (nudge, hint), counted toward context use."""
    client.add_user(text)
    state.added_chars += len(text)


def _start_task(
    client: Any,
    executor: Executor,
    user_text: str,
    images: list[str] | None,
    auto_screenshot: bool,
    emit: EventFn,
) -> TaskState:
    """Add the user's request to the chat, with a screenshot when it needs vision."""
    attach = list(images) if images else []
    # Auto-capture only when this request needs screen vision (not on every hello/script).
    vision = bool(attach) or (auto_screenshot and needs_screen_vision(user_text))
    # "open X and tell me what you see": a capture now shows the screen before X is open.
    # The refresh after open_app / web_open becomes the first screenshot instead.
    if vision and not attach and opens_first(user_text):
        emit("screenshot", "deferred")
    elif vision and auto_screenshot and not attach:
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
    return TaskState(user_text, vision=vision)


def _recover_leaked_calls(client: Any, executor: Executor, turn: Any, emit: EventFn) -> None:
    """Turn tool calls the model wrote as text into real ones, in the turn and the history."""
    recover = getattr(client, "recover_tool_calls", None)
    if turn.tool_calls or not callable(recover):
        return
    calls = leaked_tool_calls(turn.content, executor.registry)
    if not calls:
        return
    emit("recovered", turn.content)
    recover(calls)  # history: the text is dropped so the model does not copy it
    turn.tool_calls = calls
    turn.content = ""


def _stopped(client: Any, round_no: int) -> TaskResult:
    # Leave a note so the next request knows this one was cut short.
    client.add_user("[The user stopped the previous request before it finished.]")
    return TaskResult("stopped", STOPPED_TEXT, round_no)


def _should_nudge(state: TaskState, turn: Any) -> bool:
    """Once per task: the model announced an action but stopped without a tool call."""
    return (
        not turn.tool_calls
        and state.used_tools
        and not state.nudged
        and promises_action(turn.content)
    )


def _final_answer(turn: Any, round_no: int, emit: EventFn) -> TaskResult:
    # Small models sometimes end a task with an empty message. The UI waits for a
    # non-empty reply to stop its "thinking" dots, so never return an empty one.
    text = turn.content if turn.content.strip() else EMPTY_REPLY
    emit("final", text)
    return TaskResult("done", text, round_no)


def _skip_plan(client: Any, plan: list[dict[str, Any]]) -> None:
    """Every tool call still needs an answer before the next request."""
    for step in plan:
        name = str(step.get("name"))
        client.add_tool_result(name, _skipped_message(name))


def _remember(state: TaskState, step: dict[str, Any], r: StepResult) -> None:
    """One line for the handoff note, and track whether the element list is still valid."""
    detail = _clip(r.output) if r.ok else _clip(r.error or "")
    line = f"{r.name}({_clip(step.get('arguments') or {}, 60)}) -> {r.status}"
    state.steps.append(f"{line}: {detail}" if detail and detail != "null" else line)
    if r.ok and r.name == "list_elements":
        state.last_elements = r.to_message()
    elif r.ok and r.name in _STALES_ELEMENTS:
        state.last_elements = None  # the screen changed, old numbers are invalid


def _report_results(
    client: Any,
    state: TaskState,
    plan: list[dict[str, Any]],
    results: list[StepResult],
    emit: EventFn,
) -> None:
    """Every tool call must get a tool message back, run or not."""
    by_index = {r.index: r for r in results}
    for i, step in enumerate(plan):
        name = str(step.get("name"))
        r = by_index.get(i)
        message = r.to_message() if r else _skipped_message(name)
        client.add_tool_result(name, message)
        state.added_chars += len(message)
        if r:
            emit("result", r)
            _remember(state, step, r)
    _compact_history(client)


def _ends_turn_text(executor: Executor, results: list[StepResult]) -> str | None:
    """An action flagged ends_turn (e.g. provide_script) IS the final reply."""
    finals = [r for r in results if executor.registry.get(r.name).ends_turn]
    if not finals:
        return None
    return json.dumps(finals[-1].output, ensure_ascii=False)  # compact, one line


def _refresh_screen(client: Any, executor: Executor, emit: EventFn) -> None:
    b64 = _capture_b64(executor, emit)
    if b64:
        _drop_prior_images(client)
        client.add_user("Current screen.", images=[b64])
        emit("screenshot", "auto")


def _run_follow_ups(
    client: Any,
    executor: Executor,
    state: TaskState,
    plan: list[dict[str, Any]],
    emit: EventFn,
) -> bool:
    """Run the automatic follow-up steps. True when a fresh element list was added."""
    add_calls = getattr(client, "add_auto_tool_calls", None)
    steps = follow_up_plan(plan, executor.registry) if callable(add_calls) else []
    if not steps:
        return False
    # Recorded as tool calls of the model's last turn, so each result answers a call.
    add_calls([{"function": {"name": s["name"], "arguments": s["arguments"]}} for s in steps])
    emit("follow_up", steps)
    results = executor.run(steps)
    _report_results(client, state, steps, results, emit)
    return any(r.ok and r.name == "list_elements" for r in results)


def _check_repeats(
    client: Any,
    state: TaskState,
    plan: list[dict[str, Any]],
    results: list[StepResult],
    emit: EventFn,
) -> None:
    """Tell the model when it repeats a call that already failed in this task."""
    repeated: list[str] = []
    for r in results:
        if r.ok or r.status == "declined" or not 0 <= r.index < len(plan):
            continue
        sig = _signature(plan[r.index])
        if sig in state.failed_calls:
            repeated.append(sig)
        state.failed_calls.add(sig)
    if repeated:
        emit("stuck", repeated)
        _add_note(client, state, STUCK_HINT.format(calls=", ".join(repeated)))


def _needs_handoff(client: Any, state: TaskState, handoff_at: float) -> bool:
    limit = getattr(client, "context_limit", None)
    if not limit or not callable(getattr(client, "clear", None)):
        return False
    if state.handoffs >= MAX_HANDOFFS or len(state.steps) <= state.handoff_mark:
        return False  # nothing new to carry over: a restart would not free anything
    used = int(getattr(client, "context_used", 0) or 0)
    return used + state.added_chars // _CHARS_PER_TOKEN >= handoff_at * limit


def handoff_note(state: TaskState) -> str:
    """The first message of the fresh chat: the request, the steps so far, the screen."""
    steps = state.steps[-_HANDOFF_STEPS:]
    lines = [
        "[This task continues in a fresh chat: older messages were removed to free space.]",
        f"Original request: {state.user_text}",
        "Steps done so far (oldest first):",
    ]
    hidden = len(state.steps) - len(steps)
    if hidden:
        lines.append(f"- ({hidden} earlier steps not shown)")
    lines += [f"- {s}" for s in steps]
    if state.last_elements:
        lines += ["Latest list_elements result (its numbers are still valid):", state.last_elements]
    lines.append("Continue the task from here. Do not repeat steps that already succeeded.")
    return "\n".join(lines)


def _record_handoff(state: TaskState, note: str, emit: EventFn) -> None:
    state.handoffs += 1
    state.handoff_mark = len(state.steps)
    state.added_chars = len(note)
    emit("handoff", state.handoffs)


def _handoff(client: Any, state: TaskState, emit: EventFn) -> None:
    """Restart the chat with a handoff note. All tool calls are answered at this point.

    No screenshot is attached: an image would refill much of the space just freed.
    The model can call screenshot itself when it needs to see the screen.
    """
    note = handoff_note(state)
    escalate = getattr(client, "escalate", None)
    if callable(escalate):
        info = escalate(note, reason="context")
        if info:
            _record_handoff(state, note, emit)
            emit("model_switch", info)
            return
    client.clear()
    client.add_user(note)
    _record_handoff(state, note, emit)


def _escalate_on_api_error(client: Any, state: TaskState, emit: EventFn, exc: Exception) -> bool:
    """Try primary→fallback→local after an OpenRouter failure. True if task can continue."""
    escalate = getattr(client, "escalate", None)
    if not callable(escalate):
        return False
    note = handoff_note(state)
    skip_api = bool(getattr(exc, "skip_api_fallback", False))
    info = escalate(note, reason=f"api_error:{exc}", skip_api_fallback=skip_api)
    if not info:
        return False
    _record_handoff(state, note, emit)
    emit("model_switch", info)
    return True


def _run_round(
    client: Any,
    executor: Executor,
    state: TaskState,
    turn: Any,
    *,
    auto_screenshot: bool,
    auto_follow_up: bool,
    max_failed_rounds: int,
    emit: EventFn,
    stop_requested: StopFn,
) -> TaskResult | None:
    """Run the model's tool calls. Returns a TaskResult when the task ends, else None."""
    state.used_tools = True
    plan = plan_from_tool_calls(turn.tool_calls)
    emit("plan", plan)
    if stop_requested():
        _skip_plan(client, plan)
        return _stopped(client, state.round_no)
    results: list[StepResult] = executor.run(plan)
    _report_results(client, state, plan, results, emit)

    had_screenshot = any(r.ok and r.name == "screenshot" for r in results)
    if had_screenshot:
        state.vision = True
    _attach_screenshot_images(client, results)

    if any(r.status == "declined" for r in results):
        return TaskResult(
            "declined", "A step needed your approval and was not approved.", state.round_no
        )

    if len(results) == len(plan) and all(r.ok for r in results):
        # Stop on an ends_turn action instead of asking the model to comment on it again.
        text = _ends_turn_text(executor, results)
        if text is not None:
            emit("final", text)
            return TaskResult("done", text, state.round_no)
        state.failed_rounds = 0
        listed = (
            auto_follow_up
            and not stop_requested()
            and _run_follow_ups(client, executor, state, plan, emit)
        )
        # Mid-task refresh only while this task is already using vision, the plan did
        # not just capture, a UI-changing action ran, and no fresh element list came.
        ui_changed = any(r.ok and r.name in _UI_CHANGING for r in results)
        if auto_screenshot and state.vision and not had_screenshot and ui_changed and not listed:
            _refresh_screen(client, executor, emit)
        return None

    _check_repeats(client, state, plan, results, emit)
    state.failed_rounds += 1
    state.last_errors = "; ".join(r.error or r.status for r in results if not r.ok)
    if state.failed_rounds > max_failed_rounds:
        return TaskResult(
            "failed", f"Gave up after repeated failures: {state.last_errors}", state.round_no
        )
    return None


def run_task(
    client: Any,  # core.chat_client.ChatClient (anything with add_user/chat/add_tool_result)
    executor: Executor,
    user_text: str,
    *,
    images: list[str] | None = None,
    auto_screenshot: bool = True,
    auto_follow_up: bool = True,
    handoff_at: float = HANDOFF_AT,
    max_rounds: int = 20,
    max_failed_rounds: int = 2,
    on_event: EventFn | None = None,
    should_stop: StopFn | None = None,
) -> TaskResult:
    emit: EventFn = on_event or (lambda kind, data: None)
    stop_requested: StopFn = should_stop or (lambda: False)
    state = _start_task(client, executor, user_text, images, auto_screenshot, emit)
    tools = executor.registry.to_tools()  # what the model sees == what we enforce

    for round_no in range(1, max_rounds + 1):
        state.round_no = round_no
        if stop_requested():
            return _stopped(client, round_no - 1)
        if _needs_handoff(client, state, handoff_at):
            _handoff(client, state, emit)
        try:
            turn = client.chat(tools=tools)
        except ChatCancelled:
            return _stopped(client, round_no)
        except OpenRouterError as exc:
            if getattr(exc, "retriable", False) and _escalate_on_api_error(
                client, state, emit, exc
            ):
                continue
            return TaskResult("failed", f"Core AI error: {exc}", round_no)
        state.added_chars = 0  # the reply's token count now covers everything sent
        _recover_leaked_calls(client, executor, turn, emit)
        if _should_nudge(state, turn):
            state.nudged = True
            emit("nudge", turn.content)
            _add_note(client, state, NUDGE)
            continue
        if not turn.tool_calls:
            return _final_answer(turn, round_no, emit)

        result = _run_round(
            client,
            executor,
            state,
            turn,
            auto_screenshot=auto_screenshot,
            auto_follow_up=auto_follow_up,
            max_failed_rounds=max_failed_rounds,
            emit=emit,
            stop_requested=stop_requested,
        )
        if result is not None:
            return result

    return TaskResult("max_rounds", f"Stopped after {max_rounds} rounds.", max_rounds)
