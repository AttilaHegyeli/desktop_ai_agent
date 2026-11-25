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
    # Injected: filled by the orchestrator from what it knows (e.g. the user's request),
    # never by the model. Left out of the model's tool list, still validated and logged.
    injected: bool = False
    # Private: the audit log keeps only its length (typed text can be a password).
    private: bool = False

    def __post_init__(self) -> None:
        if self.type not in _PY_TYPES:
            raise ValueError(f"Param '{self.name}': unsupported type '{self.type}'")
        if self.injected and self.required:
            raise ValueError(f"Param '{self.name}': an injected parameter must be optional")

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

    def inject(self, args: Any, values: Mapping[str, Any]) -> Any:
        """`args` with every injected parameter set from `values`.

        A value the model wrote for an injected parameter is always dropped: only our
        code may fill these. Non-object `args` are returned unchanged, so validation
        still rejects them with its usual message.
        """
        if args is not None and not isinstance(args, Mapping):
            return args
        injected = {p.name for p in self.params if p.injected}
        out = {k: v for k, v in (args or {}).items() if k not in injected}
        out.update({name: values[name] for name in injected if name in values})
        return out

    def to_tool(self) -> dict[str, Any]:
        """Tool definition in the OpenAI/Ollama function-calling format. Injected
        parameters are left out: the model neither sees nor sets them."""
        visible = [p for p in self.params if not p.injected]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.json_schema() for p in visible},
                    "required": [p.name for p in visible if p.required],
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

    def inject(self, plan: Any, values: Mapping[str, Any]) -> Any:
        """A copy of `plan` with injected parameters filled from `values`.

        Steps that are malformed or name an unknown action are left as they are, so
        the executor rejects them with its usual message.
        """
        if not isinstance(plan, (list, tuple)):
            return plan
        out: list[Any] = []
        for step in plan:
            name = step.get("name") if isinstance(step, Mapping) else None
            if not isinstance(name, str) or name not in self._specs:
                out.append(step)
                continue
            out.append({**step, "arguments": self._specs[name].inject(step.get("arguments"), values)})
        return out


def _request_param(purpose: str) -> Param:
    # No max_length: it is our own copy of the user's text, and a long request must
    # never make a call fail.
    return Param("request", "string", f"The user's latest message, {purpose}", required=False, injected=True)


_CLOUD = Param(
    "cloud", "boolean", "The selected model runs in the cloud (private pages need the user's OK)",
    required=False, injected=True,
)


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
                    "'vs code'. Only programs found in the executable index can be opened. "
                    "If it is already running, its window is brought to the front instead. "
                    "Waits up to 10 s for the window to appear."
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
                    "Open an http or https URL in a new tab of the agent's own browser (the user's "
                    "default browser with the agent's own profile). Use when the user wants to go to "
                    "a site, open GitHub/YouTube/a link, or play something via a web page. Build a "
                    "sensible https URL. For answering questions with facts, prefer web_search "
                    "instead. Waits for the page to load."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("url", "string", "Full http or https URL to open", max_length=2000),
                ),
            ),
            ActionSpec(
                name="list_elements",
                description=(
                    "List the clickable elements on screen (buttons, icons, menu items, tabs, links, "
                    "text boxes) as numbered lines with name and rough position. Elements whose "
                    "names match the user's request come first. area: active = the window in front "
                    "(default; a web page in front is listed from the page itself), taskbar, desktop "
                    "(desktop icons), all (grouped under [window] headers), page = the web page in "
                    "the agent's browser. Call this before move_mouse, click or type_text."
                ),
                risk=Risk.LOW,
                params=(
                    Param(
                        "area",
                        "string",
                        "Which part of the screen to list (default active)",
                        required=False,
                        enum=("active", "taskbar", "desktop", "all", "page"),
                    ),
                    _request_param("used to put matching elements first"),
                    _CLOUD,
                ),
            ),
            ActionSpec(
                name="browser_read",
                description=(
                    "Read the text of the web page in the agent's browser (the page in front, or "
                    "its current tab). Long pages come in parts. Use find to get only the text "
                    "around a word or phrase."
                ),
                risk=Risk.LOW,
                params=(
                    Param("find", "string", "Word or phrase to look for (optional)", required=False, max_length=100),
                    Param("part", "integer", "Which part of a long page (default 1)", required=False, minimum=1, maximum=50),
                    _request_param("used to see whether they agreed to a cloud warning"),
                    _CLOUD,
                ),
            ),
            ActionSpec(
                name="browser_go",
                description="Go back, go forward or reload the current page in the agent's browser.",
                risk=Risk.MEDIUM,
                params=(
                    Param("action", "string", "What to do", enum=("back", "forward", "reload")),
                ),
            ),
            ActionSpec(
                name="site_map",
                description=(
                    "Site map of the site open in the agent's browser: where something is (a setting, "
                    "a section) and an overview to introduce the site. action: map = explore and save "
                    "the site (only when the user asks to map it; takes about 2 minutes; add find when "
                    "they also asked where something is), find = the best matches for find, and match 1 "
                    "is shown on the page, show = show match number 2 or 3 of the last find, outline = "
                    "overview of the mapped site."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("action", "string", "What to do", enum=("map", "find", "show", "outline")),
                    Param("find", "string", "What to look for, in the user's words", required=False, max_length=100),
                    Param("number", "integer", "Match number from the last find (show)", required=False, minimum=1, maximum=3),
                    _request_param("used to check that they asked for a map"),
                    _CLOUD,
                ),
            ),
            ActionSpec(
                name="list_windows",
                description=(
                    "List the open windows (title, program, minimized/maximized) as numbered lines, "
                    "front first, with the agent browser's tabs under its window. Use it when you "
                    "need a window's or tab's number for window_control."
                ),
                risk=Risk.LOW,
            ),
            ActionSpec(
                name="window_control",
                description=(
                    "Focus (bring to front), minimize, maximize, restore or close an open window, "
                    "or switch to (focus) or close a tab of the agent's browser, by name (title, "
                    "program or site, e.g. spotify, youtube) or by number from list_windows. If "
                    "several or none match, nothing changes and you get numbered entries to choose "
                    "from. close works like the X button."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("action", "string", "What to do", enum=("focus", "minimize", "maximize", "restore", "close")),
                    Param("name", "string", "Window or tab title, program or site; pass this or number", required=False, max_length=100),
                    Param("number", "integer", "Window or tab number from the latest list; pass this or name", required=False, minimum=1),
                ),
            ),
            ActionSpec(
                name="move_mouse",
                description=(
                    "Move the mouse pointer onto one element from the latest list_elements "
                    "result, by its number. On a web page it scrolls to the element and outlines "
                    "it instead. It does not click."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("element", "integer", "Element number from the latest list_elements result", minimum=1),
                ),
            ),
            ActionSpec(
                name="click",
                description=(
                    "Click one element from the latest list_elements result, by its number (on a "
                    "web page it clicks inside the page). Only click what the user asked for. After "
                    "a click the list is cleared: call list_elements again before any further mouse action."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("element", "integer", "Element number from the latest list_elements result", minimum=1),
                    Param(
                        "button",
                        "string",
                        "Mouse button (default left)",
                        required=False,
                        enum=("left", "right"),
                    ),
                    Param(
                        "double",
                        "boolean",
                        "Double-click instead of a single click, e.g. to open a desktop icon (default false)",
                        required=False,
                    ),
                    _request_param("used to see whether they answered a cookie question"),
                ),
            ),
            ActionSpec(
                name="type_text",
                description=(
                    "Enter text into an input field (Edit, SearchBox, ComboBox or Document element) "
                    "from the latest list_elements result. It never types into password fields and "
                    "presses Enter only with submit=true, which works only in web search fields."
                ),
                risk=Risk.MEDIUM,
                params=(
                    Param("element", "integer", "Input field number from the latest list_elements result", minimum=1),
                    Param("text", "string", "The text to enter", max_length=2000, private=True),
                    Param(
                        "submit",
                        "boolean",
                        "Press Enter afterwards to run a search (web search fields only; default false)",
                        required=False,
                    ),
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