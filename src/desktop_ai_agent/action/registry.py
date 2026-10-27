"""Action registry: the single source of truth for what the AI may ask for.

Pure data + validation. No handlers, no OS access, so it is safe to import
from anywhere (e.g. the orchestrator uses it to build the model's tool list).

Risk levels are set HERE, by us. The model can never change them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Risk(str, Enum):
    LOW = "low"        # runs freely (screenshot, wait)
    MEDIUM = "medium"  # runs, but is logged (click, type)
    HIGH = "high"      # needs user confirmation (delete, send, pay)


class ValidationError(ValueError):
    """Raised when an action name or its arguments are not acceptable.

    The message is written to be sent back to the model so it can self-correct.
    """


_PY_TYPES: dict[str, type | tuple[type, ...]] = {
    "integer": int,
    "number": (int, float),
    "string": str,
    "boolean": bool,
}


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # "integer" | "number" | "string" | "boolean"
    description: str
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    enum: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.type not in _PY_TYPES:
            raise ValueError(f"Param '{self.name}': unsupported type '{self.type}'")

    def check(self, value: Any) -> Any:
        # bool is a subclass of int in Python; never accept True/False as a number.
        if isinstance(value, bool) and self.type != "boolean":
            raise ValidationError(f"'{self.name}' must be {self.type}, got boolean")
        if not isinstance(value, _PY_TYPES[self.type]):
            raise ValidationError(
                f"'{self.name}' must be {self.type}, got {type(value).__name__}"
            )
        if self.minimum is not None and value < self.minimum:
            raise ValidationError(f"'{self.name}' must be >= {self.minimum}, got {value}")
        if self.maximum is not None and value > self.maximum:
            raise ValidationError(f"'{self.name}' must be <= {self.maximum}, got {value}")
        if self.max_length is not None and len(value) > self.max_length:
            raise ValidationError(
                f"'{self.name}' is too long ({len(value)} > {self.max_length} characters)"
            )
        if self.enum is not None and value not in self.enum:
            raise ValidationError(
                f"'{self.name}' must be one of {list(self.enum)}, got '{value}'"
            )
        return value

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.enum is not None:
            schema["enum"] = list(self.enum)
        return schema


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    risk: Risk
    params: tuple[Param, ...] = ()
    # True = this action's result IS the final reply; the agent loop stops after it.
    ends_turn: bool = False

    def validate(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        """Return cleaned arguments or raise ValidationError. Deny by default."""
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ValidationError(
                f"{self.name}: arguments must be an object, got {type(args).__name__}"
            )
        known = {p.name: p for p in self.params}
        unknown = sorted(set(args) - set(known))
        if unknown:
            raise ValidationError(
                f"{self.name}: unknown parameter(s) {unknown}. Allowed: {sorted(known)}"
            )
        clean: dict[str, Any] = {}
        for p in self.params:
            if p.name in args:
                try:
                    clean[p.name] = p.check(args[p.name])
                except ValidationError as exc:
                    raise ValidationError(f"{self.name}: {exc}") from None
            elif p.required:
                raise ValidationError(f"{self.name}: missing required parameter '{p.name}'")
        return clean

    def to_tool(self) -> dict[str, Any]:
        """Tool definition in the OpenAI/Ollama function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.json_schema() for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                    "additionalProperties": False,
                },
            },
        }


class ActionRegistry:
    def __init__(self, specs: tuple[ActionSpec, ...] | list[ActionSpec] = ()) -> None:
        self._specs: dict[str, ActionSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ActionSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Duplicate action name: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: Any) -> ActionSpec:
        if not isinstance(name, str) or name not in self._specs:
            raise ValidationError(
                f"Unknown action '{name}'. Available: {sorted(self._specs)}"
            )
        return self._specs[name]

    def names(self) -> list[str]:
        return sorted(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def to_tools(self) -> list[dict[str, Any]]:
        """The exact list handed to the model, so what it sees == what we enforce."""
        return [self._specs[n].to_tool() for n in self.names()]


def build_default_registry() -> ActionRegistry:
    """Step 1 of the build order: safe actions only. Add click/type later."""
    return ActionRegistry(
        [
            ActionSpec(
                name="screenshot",
                description=(
                    "Capture the screen (or a region) and return it as an image. "
                    "Primary way to answer on-screen questions — use this whenever "
                    "the answer might be visible on the desktop. Default scope is "
                    "primary monitor. Use monitor/region only when the user names "
                    "a specific display or area."
                ),
                risk=Risk.LOW,
                params=(
                    Param(
                        "scope",
                        "string",
                        "What to capture: primary (default), all monitors, one monitor, or a region",
                        required=False,
                        enum=("primary", "all", "monitor", "region"),
                    ),
                    Param(
                        "monitor",
                        "integer",
                        "1-based monitor index when scope is monitor",
                        required=False,
                        minimum=1,
                    ),
                    Param(
                        "x",
                        "integer",
                        "Region left (virtual-desktop coords) when scope is region",
                        required=False,
                    ),
                    Param(
                        "y",
                        "integer",
                        "Region top (virtual-desktop coords) when scope is region",
                        required=False,
                    ),
                    Param(
                        "width",
                        "integer",
                        "Region width when scope is region",
                        required=False,
                        minimum=1,
                    ),
                    Param(
                        "height",
                        "integer",
                        "Region height when scope is region",
                        required=False,
                        minimum=1,
                    ),
                ),
            ),
            ActionSpec(
                name="wait",
                description="Pause for a number of seconds (max 30).",
                risk=Risk.LOW,
                params=(
                    Param("seconds", "number", "How long to wait", minimum=0, maximum=30),
                ),
            ),
            ActionSpec(
                name="open_app",
                description=(
                    "Open an installed Windows application by name, for example 'steam' or "
                    "'vs code'. Only programs found in the executable index can be opened."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("name", "string", "Application name, e.g. steam or vs code", max_length=100),
                ),
            ),
            ActionSpec(
                name="web_search",
                description=(
                    "Search the web and return short result snippets. Use this to answer "
                    "questions that need current facts (weather, news, definitions). "
                    "After results return, answer the user in plain text from those snippets. "
                    "Do not use this when the user only wants a page or site opened."
                ),
                risk=Risk.LOW,
                params=(
                    Param("query", "string", "Search query", max_length=200),
                    Param(
                        "max_results",
                        "integer",
                        "How many results to return (default 3)",
                        required=False,
                        minimum=1,
                        maximum=5,
                    ),
                ),
            ),
            ActionSpec(
                name="web_open",
                description=(
                    "Open an http or https URL in the user's default browser. Use when the "
                    "user wants to go to a site, open GitHub/YouTube/a link, or play something "
                    "via a web page. Build a sensible https URL. For answering questions with "
                    "facts, prefer web_search instead."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("url", "string", "Full http or https URL to open", max_length=2000),
                ),
            ),
            ActionSpec(
                name="provide_script",
                description=(
                    "Show the user a Python script as JSON in the chat. Use it only after "
                    "the user has said they want a script. The code is displayed only and "
                    "is never run."
                ),
                risk=Risk.LOW,
                params=(
                    Param("script", "string", "Short description of what the code does", max_length=200),
                    Param("code", "string", "Python code only", max_length=20000),
                ),
                ends_turn=True,
            ),
        ]
    )