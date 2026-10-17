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

import json
from dataclasses import dataclass
from typing import Any, Callable

from desktop_ai_agent.action.executor import Executor, StepResult, plan_from_tool_calls

EventFn = Callable[[str, Any], None]


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


def run_task(
    client: Any,  # core.chat_client.ChatClient (anything with add_user/chat/add_tool_result)
    executor: Executor,
    user_text: str,
    *,
    images: list[str] | None = None,
    max_rounds: int = 8,
    max_failed_rounds: int = 2,
    on_event: EventFn | None = None,
) -> TaskResult:
    emit: EventFn = on_event or (lambda kind, data: None)
    client.add_user(user_text, images)
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
            continue

        failed_rounds += 1
        last_errors = "; ".join(r.error or r.status for r in results if not r.ok)
        if failed_rounds > max_failed_rounds:
            return TaskResult("failed", f"Gave up after repeated failures: {last_errors}", round_no)

    return TaskResult("max_rounds", f"Stopped after {max_rounds} rounds.", max_rounds)