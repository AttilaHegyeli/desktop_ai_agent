"""Per-task bookkeeping for the agent loop.

One TaskState lives for one run_task call. It holds the counters and flags the
loop needs between rounds, so the round logic can live in small functions
instead of one long loop body.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskState:
    user_text: str
    vision: bool = False  # this task uses screen vision (auto screenshots allowed)
    round_no: int = 0
    failed_rounds: int = 0  # consecutive rounds where a step did not succeed
    last_errors: str = ""
    nudged: bool = False  # the "you promised an action" nudge was already sent
    used_tools: bool = False
