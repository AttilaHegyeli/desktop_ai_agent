"""Per-task bookkeeping for the agent loop.

One TaskState lives for one run_task call. It holds the counters and flags the
loop needs between rounds, so the round logic can live in small functions
instead of one long loop body. It also keeps a short log of every step, which
becomes the handoff note when the chat is restarted to free context.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskState:
    user_text: str
    vision: bool = False  # this task uses screen vision (auto screenshots allowed)
    round_no: int = 0
    failed_rounds: int = 0  # consecutive rounds where a step did not succeed
    last_errors: str = ""
    nudged: bool = False  # the "you promised an action" nudge was already sent
    used_tools: bool = False
    steps: list[str] = field(default_factory=list)  # one line per executed step
    last_elements: str | None = None  # newest list_elements result while still valid
    failed_calls: set[str] = field(default_factory=set)  # signatures of calls that failed
    added_chars: int = 0  # text added to the chat since the model last replied
    handoffs: int = 0
    handoff_mark: int = 0  # len(steps) at the last handoff
