"""Routes core AI between local Gemma (Ollama) and OpenRouter models."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient, ChatTurn
from desktop_ai_agent.core.llm_protocol import UsageFn
from desktop_ai_agent.core.model_catalog import (
    ModelChoice,
    choice_by_id,
    fallback_model_id,
    load_catalog,
    openrouter_base_url,
    openrouter_selection_id,
    parse_selection,
    selected_id,
)
from desktop_ai_agent.core.openrouter_client import OpenRouterClient
from desktop_ai_agent.orchestrator.prompts import system_prompt_for

LogFn = Callable[[str], None]


class CoreRouter:
    """Active LLM backend with cascade: primary API → fallback API → local Gemma."""

    def __init__(
        self,
        *,
        setup: dict[str, Any],
        local_host: str,
        api_key: str | None = None,
        selection: str | None = None,
        on_usage: UsageFn | None = None,
    ) -> None:
        self.setup = setup
        ollama = setup.get("ollama") or {}
        self.local_model = str(ollama.get("base_model") or "gemma4")
        self.local_host = local_host.rstrip("/")
        self.api_key = (api_key or "").strip() or None
        self.catalog = load_catalog(setup, local_model=self.local_model)
        self.fallback_api_model = fallback_model_id(setup)
        self.base_url = openrouter_base_url(setup)
        self.on_usage = on_usage
        self._selection = selection or selected_id(setup, local_model=self.local_model)
        self._tier = "primary"  # primary | fallback_api | local
        self._client: Any = None
        self._build_client(self._selection, tier="primary")

    # ---- public metadata -----------------------------------------------------
    @property
    def selection_id(self) -> str:
        return self._selection

    @property
    def active_label(self) -> str:
        choice = choice_by_id(self.catalog, self._selection)
        if self._tier == "fallback_api":
            return f"API · {self.fallback_api_model} (fallback)"
        if self._tier == "local" and not (choice and choice.provider == "local"):
            return f"Local · {self.local_model} (fallback)"
        if choice:
            return choice.display
        return self._selection

    @property
    def provider(self) -> str:
        if self._tier == "local":
            return "local"
        return "openrouter"

    @property
    def model(self) -> str:
        return getattr(self._client, "model", self.local_model)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._client.messages

    @property
    def context_used(self) -> int:
        return int(getattr(self._client, "context_used", 0) or 0)

    @property
    def context_limit(self) -> int | None:
        return getattr(self._client, "context_limit", None)

    def set_usage_listener(self, fn: UsageFn | None) -> None:
        self.on_usage = fn
        self._client.on_usage = fn

    def dropdown_choices(self) -> list[ModelChoice]:
        return list(self.catalog)

    def has_api_key(self) -> bool:
        return bool(self.api_key)

    # ---- selection / escalate ------------------------------------------------
    def set_selection(self, selection_id: str, *, reset: bool = True) -> str:
        """Switch user-chosen primary model. Returns the label shown in the UI."""
        self._selection = selection_id
        self._tier = "primary"
        self._build_client(selection_id, tier="primary")
        if reset:
            self.clear()
        return self.active_label

    def escalate(
        self,
        handoff_note: str,
        *,
        reason: str = "context",
        skip_api_fallback: bool = False,
    ) -> dict[str, Any] | None:
        """Move to the next backend and seed a handoff note.

        Cascade (context / rate-limit): primary OpenRouter → fallback OpenRouter → local Gemma.
        Billing / key-limit errors skip the other API model and go straight to local.
        Returns switch info for UI events, or None when the caller should do a
        same-model handoff (already on local).
        """
        provider, _ = parse_selection(self._selection)
        if provider == "local" or self._tier == "local":
            return None

        try_api_fallback = (
            not skip_api_fallback
            and self._tier == "primary"
            and bool(self.api_key)
            and parse_selection(self._selection)[1] != self.fallback_api_model
        )
        if try_api_fallback:
            target = openrouter_selection_id(self.fallback_api_model)
            self._tier = "fallback_api"
            self._build_client(target, tier="fallback_api")
            self.clear()
            self.add_user(handoff_note)
            return {
                "label": self.active_label,
                "selection": target,
                "tier": self._tier,
                "reason": reason,
            }

        # Billing, fallback exhausted, or primary already is the fallback model → local Gemma
        local_id = f"local:{self.local_model}"
        self._tier = "local"
        self._build_client(local_id, tier="local")
        self.clear()
        self.add_user(handoff_note)
        return {
            "label": self.active_label,
            "selection": local_id,
            "tier": self._tier,
            "reason": reason,
        }

    # ---- LlmClient surface (delegate) ----------------------------------------
    def clear(self) -> None:
        self._client.clear()

    def abort(self) -> None:
        abort = getattr(self._client, "abort", None)
        if abort:
            abort()

    def drop_images(self) -> None:
        self._client.drop_images()

    def compact_tool_results(self, tool_name: str, stub: str, keep_last: int = 1) -> int:
        return self._client.compact_tool_results(tool_name, stub, keep_last=keep_last)

    def add_user(self, text: str, images: list[str] | None = None) -> None:
        self._client.add_user(text, images=images)

    def add_tool_result(self, tool_name: str, content: str) -> None:
        self._client.add_tool_result(tool_name, content)

    def add_auto_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        add = getattr(self._client, "add_auto_tool_calls", None)
        if add:
            add(calls)

    def recover_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        recover = getattr(self._client, "recover_tool_calls", None)
        if recover:
            recover(calls)

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        return self._client.chat(tools=tools, on_chunk=on_chunk)

    # ---- builders ------------------------------------------------------------
    def _build_client(self, selection_id: str, *, tier: str) -> None:
        provider, model_id = parse_selection(selection_id)
        if tier == "local" or provider == "local":
            prompt = system_prompt_for("local")
            self._client = ChatClient(
                model_id or self.local_model,
                self.local_host,
                options={"temperature": 0.2, "num_ctx": 8192},
                system=prompt,
            )
        else:
            if not self.api_key:
                # No key: force local rather than failing hard at create time.
                self._tier = "local"
                self._build_client(f"local:{self.local_model}", tier="local")
                return
            choice = choice_by_id(self.catalog, selection_id)
            ctx = choice.context_tokens if choice else 128_000
            mid = model_id
            if tier == "fallback_api":
                mid = self.fallback_api_model
                fb = choice_by_id(self.catalog, openrouter_selection_id(mid))
                if fb:
                    ctx = fb.context_tokens
            prompt = system_prompt_for("openrouter")
            self._client = OpenRouterClient(
                mid,
                self.api_key,
                base_url=self.base_url,
                system=prompt,
                context_tokens=ctx,
            )
        self._client.on_usage = self.on_usage

    @classmethod
    def from_setup(
        cls,
        setup: dict[str, Any],
        *,
        local_host: str,
        api_key: str | None = None,
    ) -> "CoreRouter":
        return cls(setup=setup, local_host=local_host, api_key=api_key)
