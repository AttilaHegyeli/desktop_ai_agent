# Action layer

The action layer is the **only** place that may change the machine. The model proposes tool calls; this layer decides what actually runs.

## Pieces

| File | Job |
|------|-----|
| `action/registry.py` | Allowed actions: name, description, params, limits, risk. Source of truth for the model’s tool list. |
| `action/executor.py` | Gate: exists? args valid? risk OK? then log and run. |
| `action/handlers.py` | Side effects. No policy logic. Only the executor imports this. |
| `action/audit_log.py` | Append-only event log. |
| `action/tools/` | Shared helpers (e.g. screenshot capture). |

Flow:

```text
model tool calls → plan_from_tool_calls → Executor → handlers → results → model
```

## Current actions (v0.1.0)

| Action | Risk | Behavior |
|--------|------|----------|
| `wait(seconds)` | low | Pause 0–30 seconds |
| `screenshot` | low | Full-screen PNG under `logs/screenshots/`; image attached for next turn |
| `open_app(name)` | medium | Launch an indexed `.exe` by name (e.g. `steam`, `vs code`) |
| `provide_script(script, code)` | low | Show JSON in chat; **never executes**; ends the turn |

## Safety rules

1. Unknown actions or bad arguments → rejected; reason returned to the model.
2. A whole plan is validated before anything runs.
3. Risk levels are set in the registry by us, never by the model.
4. **High** risk needs user confirmation. Confirm UI is not wired → high-risk calls are **denied**.
5. Orchestrator stop limits: 8 rounds / task, 2 consecutive failed rounds.

## Add a new action

1. Add an `ActionSpec` in `registry.py` (name, description, risk, params with limits).
2. Add the function in `handlers.py` and register it in `HANDLERS`.
3. Add a test under `tests/action/`.

The executor refuses to start if registry names and handlers do not match.

## Audit log

Path: `logs/audit.jsonl` (from the working directory where you started the app).

Typical event types: `rejected`, `declined`, `execute`, `result`.

## See also

- [Architecture](architecture.md)  
- [Demo & usage](demo.md)  
- [Testing](testing.md)  
