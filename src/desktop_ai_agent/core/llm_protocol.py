"""Shared surface the orchestrator expects from any core AI client."""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from desktop_ai_agent.core.chat_client import ChatTurn

UsageFn = Callable[[int], None]


@runtime_checkable
class LlmClient(Protocol):
    """Duck type used by run_task / AgentService. Both Ollama and OpenRouter implement this."""

    model: str
    messages: list[dict[str, Any]]
    context_used: int
    on_usage: UsageFn | None

    @property
    def context_limit(self) -> int | None: ...

    def clear(self) -> None: ...

    def abort(self) -> None: ...

    def drop_images(self) -> None: ...

    def compact_tool_results(self, tool_name: str, stub: str, keep_last: int = 1) -> int: ...

    def add_user(self, text: str, images: list[str] | None = None) -> None: ...

    def add_tool_result(self, tool_name: str, content: str) -> None: ...

    def add_auto_tool_calls(self, calls: list[dict[str, Any]]) -> None: ...

    def recover_tool_calls(self, calls: list[dict[str, Any]]) -> None: ...

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ChatTurn: ...


# Selection id prefixes used in setup.json / the UI dropdown.
LOCAL_PREFIX = "local:"
OPENROUTER_PREFIX = "openrouter:"

LOCAL_GEMMA_ID = "local:gemma4"
