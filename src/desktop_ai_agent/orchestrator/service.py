"""AgentService: the single object the UI talks to.

The UI only needs:
    service = AgentService.create(confirm=my_confirm_dialog)
    service.submit("open spotify", on_event=show_step, on_done=show_result)

Tasks run on a background thread so the UI never freezes. IMPORTANT: on_event
and on_done are called FROM THAT THREAD. In Tkinter/Qt, hand the data to the UI
thread (e.g. root.after(0, ...)) instead of touching widgets directly.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from desktop_ai_agent.action.audit_log import AuditLog
from desktop_ai_agent.action.executor import ConfirmFn, Executor, default_executor
from desktop_ai_agent.core.chat_client import ChatClient, UsageFn
from desktop_ai_agent.orchestrator.agent_loop import EventFn, TaskResult, run_task
from desktop_ai_agent.orchestrator.prompts import SYSTEM_PROMPT

DoneFn = Callable[[TaskResult], None]

CONTEXT_TOKENS = 8192  # num_ctx: the model's context window (the UI meter's 8k)


class AgentService:
    def __init__(self, client: Any, executor: Executor) -> None:
        self.client = client
        self.executor = executor
        self._lock = threading.Lock()
        self._busy = False
        self._stop = threading.Event()

    @classmethod
    def create(
        cls,
        model: str = "gemma4",
        host: str = "http://127.0.0.1:11434",
        *,
        confirm: ConfirmFn | None = None,
        audit_path: str | None = "logs/audit.jsonl",
        options: dict[str, Any] | None = None,
        system_prompt: str | None = SYSTEM_PROMPT,
    ) -> "AgentService":
        client = ChatClient(
            model,
            host,
            options=options or {"temperature": 0.2, "num_ctx": CONTEXT_TOKENS},

            system=system_prompt,  # tool calls ignore the JSON format, so no need to disable it
        )
        audit = AuditLog(audit_path) if audit_path else AuditLog()
        return cls(client, default_executor(confirm=confirm, audit=audit))

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def context_limit(self) -> int:
        return getattr(self.client, "context_limit", None) or CONTEXT_TOKENS

    @property
    def context_used(self) -> int:
        return getattr(self.client, "context_used", 0)

    def set_usage_listener(self, fn: UsageFn | None) -> None:
        """fn(tokens_used) fires while replies stream, FROM THE AGENT THREAD."""
        self.client.on_usage = fn

    def reset(self) -> None:
        """Forget the conversation (e.g. a 'New chat' button)."""
        from desktop_ai_agent.action.tools import ui_control

        self.client.clear()
        ui_control.reset()

    def stop(self) -> None:
        """Ask the running task to stop: cuts the model reply, skips pending steps.

        A step that is already executing (e.g. a long wait) finishes first.
        """
        if not self._busy:
            return
        self._stop.set()
        abort = getattr(self.client, "abort", None)
        if abort:
            abort()

    def submit(
        self,
        text: str,
        on_event: EventFn | None = None,
        on_done: DoneFn | None = None,
    ) -> bool:
        """Start a task in the background. Returns False if one is already running."""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._stop.clear()

        def work() -> None:
            try:
                result = run_task(
                    self.client,
                    self.executor,
                    text,
                    on_event=on_event,
                    should_stop=self._stop.is_set,
                )
            except Exception as exc:  # never let a crash kill the UI thread silently
                result = TaskResult("failed", f"Error: {exc}", 0)
            finally:
                self._busy = False  # cleared first so on_done may submit again
            if on_done:
                on_done(result)

        threading.Thread(target=work, daemon=True, name="agent-task").start()
        return True