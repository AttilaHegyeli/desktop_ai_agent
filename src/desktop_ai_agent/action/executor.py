"""Executor: the gate between the model and the OS.

Every plan from the model passes through here. Order of checks:
  1. Plan shape (list, not empty, not too long)
  2. Every step: known action? valid arguments?   (whole plan validated BEFORE
     anything runs, so a bad plan never half-executes)
  3. Risk: high-risk steps need user confirmation (deny if no confirm callback)
  4. Log, run the handler, log the result

Only this module imports handlers.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from . import handlers as _handlers
from .audit_log import AuditLog
from .registry import (
    ActionRegistry,
    ActionSpec,
    Risk,
    ValidationError,
    build_default_registry,
)

ConfirmFn = Callable[[ActionSpec, dict[str, Any]], bool]
Handler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class StepResult:
    index: int
    name: str
    status: str  # "executed" | "rejected" | "declined" | "error"
    output: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "executed"

    def to_message(self) -> str:
        """Text sent back to the model as the tool result (so it can retry)."""
        payload: dict[str, Any] = {"action": self.name, "status": self.status}
        if self.ok:
            payload["output"] = self.output
        else:
            payload["error"] = self.error
        return json.dumps(payload, default=str)


class _PlanRejected(Exception):
    def __init__(self, index: int, name: str, message: str) -> None:
        super().__init__(message)
        self.index, self.name, self.message = index, name, message


class Executor:
    def __init__(
        self,
        registry: ActionRegistry,
        handlers: Mapping[str, Handler],
        confirm: ConfirmFn | None = None,
        audit: AuditLog | None = None,
        max_steps: int = 10,
        confirm_risks: Sequence[Risk] = (Risk.HIGH,),
    ) -> None:
        missing = set(registry.names()) - set(handlers)
        extra = set(handlers) - set(registry.names())
        if missing or extra:
            raise ValueError(
                f"Registry/handler mismatch. No handler for: {sorted(missing)}. "
                f"Handler not in registry: {sorted(extra)}"
            )
        self.registry = registry
        self._handlers = dict(handlers)
        self.confirm = confirm
        self.audit = audit or AuditLog()
        self.max_steps = max_steps
        self.confirm_risks = tuple(confirm_risks)

    # -- planning -----------------------------------------------------------
    def _validate_plan(self, plan: Any) -> list[tuple[ActionSpec, dict[str, Any]]]:
        if not isinstance(plan, (list, tuple)) or not plan:
            raise _PlanRejected(0, "?", "Plan must be a non-empty list of steps")
        if len(plan) > self.max_steps:
            raise _PlanRejected(
                0, "?", f"Plan has {len(plan)} steps; the maximum is {self.max_steps}"
            )
        steps: list[tuple[ActionSpec, dict[str, Any]]] = []
        for i, step in enumerate(plan):
            name = step.get("name") if isinstance(step, Mapping) else None
            try:
                if not isinstance(step, Mapping):
                    raise ValidationError("Each step must be an object with 'name' and 'arguments'")
                spec = self.registry.get(step.get("name"))
                args = spec.validate(step.get("arguments"))
            except ValidationError as exc:
                raise _PlanRejected(i, str(name), str(exc)) from None
            steps.append((spec, args))
        return steps

    def _ask(self, spec: ActionSpec, args: dict[str, Any]) -> bool:
        if self.confirm is None:
            return False  # deny by default
        try:
            return bool(self.confirm(spec, args))
        except Exception:
            return False

    # -- running ------------------------------------------------------------
    def run(self, plan: Any) -> list[StepResult]:
        try:
            steps = self._validate_plan(plan)
        except _PlanRejected as rej:
            self.audit.record("rejected", step=rej.index, action=rej.name, reason=rej.message)
            return [StepResult(rej.index, rej.name, "rejected", error=rej.message)]

        results: list[StepResult] = []
        for i, (spec, args) in enumerate(steps):
            if spec.risk in self.confirm_risks and not self._ask(spec, args):
                msg = "User declined, or no confirmation available"
                self.audit.record("declined", step=i, action=spec.name, risk=spec.risk.value, args=args)
                results.append(StepResult(i, spec.name, "declined", error=msg))
                break

            self.audit.record("execute", step=i, action=spec.name, risk=spec.risk.value, args=args)
            try:
                output = self._handlers[spec.name](args)
            except Exception as exc:  # handler failures must never crash the loop
                err = f"{type(exc).__name__}: {exc}"
                self.audit.record("result", step=i, action=spec.name, status="error", error=err)
                results.append(StepResult(i, spec.name, "error", error=err))
                break
            self.audit.record("result", step=i, action=spec.name, status="executed", output=output)
            results.append(StepResult(i, spec.name, "executed", output=output))
        return results


def plan_from_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """Convert the model's tool calls (Ollama objects, dicts or OpenAI style
    with JSON-string arguments) into the plain plan format run() expects.

    Output is still UNTRUSTED. run() validates every step.
    """
    plan: list[dict[str, Any]] = []
    for call in tool_calls or []:
        fn = call.get("function") if isinstance(call, Mapping) else getattr(call, "function", None)
        if isinstance(fn, Mapping):
            name, args = fn.get("name"), fn.get("arguments")
        else:
            name, args = getattr(fn, "name", None), getattr(fn, "arguments", None)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass  # left as a string, so validation rejects it with a clear message
        if isinstance(args, Mapping):
            args = dict(args)
        plan.append({"name": name, "arguments": args if args is not None else {}})
    return plan


def default_executor(confirm: ConfirmFn | None = None, audit: AuditLog | None = None) -> Executor:
    return Executor(build_default_registry(), dict(_handlers.HANDLERS), confirm=confirm, audit=audit)
