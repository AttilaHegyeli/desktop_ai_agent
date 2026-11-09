"""Curated core-AI models that support tool calling + vision for this agent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desktop_ai_agent.core.llm_protocol import LOCAL_GEMMA_ID, LOCAL_PREFIX, OPENROUTER_PREFIX

# Defaults when setup.json has no core_ai.openrouter.models block yet.
DEFAULT_OPENROUTER_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "openai/gpt-4.1",
        "label": "GPT-4.1",
        "context_tokens": 1_048_576,
    },
    {
        "id": "openai/gpt-4o",
        "label": "GPT-4o",
        "context_tokens": 128_000,
    },
    {
        "id": "anthropic/claude-sonnet-4",
        "label": "Claude Sonnet 4",
        "context_tokens": 200_000,
    },
    {
        "id": "google/gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "context_tokens": 1_048_576,
    },
    {
        "id": "google/gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "context_tokens": 1_048_576,
    },
)

DEFAULT_FALLBACK_MODEL = "google/gemini-2.5-flash"
DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class ModelChoice:
    """One row in the Core AI dropdown."""

    selection_id: str  # local:gemma4 | openrouter:<model_id>
    label: str
    provider: str  # "local" | "openrouter"
    model_id: str  # gemma4 | openai/gpt-4o
    context_tokens: int

    @property
    def display(self) -> str:
        if self.provider == "local":
            return f"Local · {self.label}"
        return f"API · {self.label}"


def openrouter_selection_id(model_id: str) -> str:
    return f"{OPENROUTER_PREFIX}{model_id}"


def parse_selection(selection_id: str) -> tuple[str, str]:
    """Return (provider, model_id)."""
    sid = (selection_id or "").strip() or LOCAL_GEMMA_ID
    if sid.startswith(OPENROUTER_PREFIX):
        return "openrouter", sid[len(OPENROUTER_PREFIX) :]
    if sid.startswith(LOCAL_PREFIX):
        return "local", sid[len(LOCAL_PREFIX) :]
    # Bare model name from older configs → treat as local.
    return "local", sid


def core_ai_section(setup: dict[str, Any] | None) -> dict[str, Any]:
    return dict((setup or {}).get("core_ai") or {})


def openrouter_section(setup: dict[str, Any] | None) -> dict[str, Any]:
    return dict(core_ai_section(setup).get("openrouter") or {})


def load_catalog(setup: dict[str, Any] | None = None, *, local_model: str = "gemma4") -> list[ModelChoice]:
    """Local Gemma first, then curated OpenRouter models from setup (or defaults)."""
    ollama = (setup or {}).get("ollama") or {}
    base = str(ollama.get("base_model") or local_model)
    local = ModelChoice(
        selection_id=f"{LOCAL_PREFIX}{base}",
        label=base,
        provider="local",
        model_id=base,
        context_tokens=int((setup or {}).get("core_ai", {}).get("local_context_tokens") or 8192),
    )
    or_cfg = openrouter_section(setup)
    raw_models = or_cfg.get("models") or list(DEFAULT_OPENROUTER_MODELS)
    choices = [local]
    for row in raw_models:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        mid = str(row["id"])
        choices.append(
            ModelChoice(
                selection_id=openrouter_selection_id(mid),
                label=str(row.get("label") or mid),
                provider="openrouter",
                model_id=mid,
                context_tokens=int(row.get("context_tokens") or 128_000),
            )
        )
    return choices


def choice_by_id(catalog: list[ModelChoice], selection_id: str) -> ModelChoice | None:
    for c in catalog:
        if c.selection_id == selection_id:
            return c
    return None


def fallback_model_id(setup: dict[str, Any] | None = None) -> str:
    return str(openrouter_section(setup).get("fallback_model") or DEFAULT_FALLBACK_MODEL)


def openrouter_base_url(setup: dict[str, Any] | None = None) -> str:
    return str(openrouter_section(setup).get("base_url") or DEFAULT_OPENROUTER_BASE).rstrip("/")


def selected_id(setup: dict[str, Any] | None = None, *, local_model: str = "gemma4") -> str:
    raw = core_ai_section(setup).get("selected")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"{LOCAL_PREFIX}{local_model}"
