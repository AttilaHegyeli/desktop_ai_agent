# Desktop AI Agent: quick guide

## What it does
The user types a request. The model (gemma4 via Ollama) picks a **tool**, the **action layer** validates and runs it, and the result is shown in the terminal.
If no tool fits, the model says so and asks "Do you want a script for it?". If the user says yes, it returns ONE line of JSON: `{"script": "...", "code": "..."}`. The script is only displayed, never executed.

## Flow
UI (`launcher.py`) -> `AgentService` -> `agent_loop` -> model (`chat_client`) -> **executor** -> **handlers**

## Where things are (`src/desktop_ai_agent/`)
- `action/registry.py`: every allowed action: name, description, parameters and limits, risk level. This is the single source of truth. The model's tool list is generated from it.
- `action/executor.py`: the gate. Checks the action exists, validates arguments, checks risk, logs, then runs.
- `action/handlers.py`: the functions that actually do things. No safety logic here. Only the executor imports this file.
- `action/audit_log.py`: writes the log.
- `orchestrator/agent_loop.py`: the loop: model -> tool calls -> executor -> results -> model.
- `orchestrator/prompts.py`: the system prompt (plain text, no model rebuild needed).
- `orchestrator/service.py`: runs tasks on a background thread for the UI.
- `core/chat_client.py`: talks to Ollama.

## Current actions
- `wait(seconds)`: low risk, 0 to 30 seconds.
- `screenshot`: low risk, **not wired yet** (returns an error).
- `provide_script(script, code)`: low risk, display only, ends the turn.

## Safety rules
- The model can only use actions in the registry. Unknown actions or bad arguments are rejected, and the reason is sent back so the model can retry.
- A plan is validated completely before anything runs.
- Risk levels (low, medium, high) are set in the registry by us, never by the model.
- High-risk actions need user confirmation. The confirm dialog is not wired yet, so they are denied.
- Limits: 8 rounds per task, 2 failed rounds in a row, then the task stops.

## Add a new action
1. Add an `ActionSpec` in `registry.py` (name, description, risk, parameters with limits).
2. Add the function to `handlers.py` and to the `HANDLERS` dict.
3. Add a test in `tests/action/`.
The executor refuses to start if the registry and handlers do not match.

## Logs
`logs/audit.jsonl` (relative to the folder the app is started from). One JSON line per event: `rejected`, `declined`, `execute`, `result`, with timestamp, action, risk and arguments.

## Run and test
- Start: `scripts/start.bat`
- Tests (PowerShell, from the project root): `$env:PYTHONPATH="src"` then `python -m unittest discover -s tests -t .`

## Known limitations
- Small local model (gemma4 E4B). It may occasionally pick the wrong action. Check the audit log.
- No screen access or mouse/keyboard actions yet.
- Results go back to the model after every action (optimization planned).
- The `gemma4-agent` profile is still created at startup but is no longer used for chatting.