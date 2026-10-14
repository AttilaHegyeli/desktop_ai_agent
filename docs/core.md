# Core AI — Current Implementation

Status: **Core is implemented**. Input / Orchestrator / Action Layer from the full architecture are not wired yet. The UI chat is a stand-in for Orchestrator input.

Pipeline today:

```text
python -m app
  → LauncherApp (tkinter)
  → SessionSetup.prepare()
       1. Index Program Files (C/D/E)
       2. Ensure Ollama + pull gemma4
       3. Build gemma4-agent from Modelfile
       4. /set nothink + format json
       5. ChatClient ready
  → User types a task
  → Ollama gemma4-agent returns JSON
  → normalize_agent_reply()
  → UI prints {"script","code"}
  → (Action Layer not built — code is not executed yet)
```

## Role of Core

Core is a **scriptwriter for a machine Action Layer**, not a chat assistant.

| Field | Meaning |
|-------|---------|
| `script` | Snake_case job id for logs/routing (`open_vs_code`) |
| `code` | Windows Python source Action will `exec` unchanged |

Contract (see `config/Modelfile.gemma4-agent` and `response_format.agent_system_prompt()`):

1. Parse the task.
2. If an app/path is needed, prefer `resolve_path` from the Program Files index.
3. Emit the smallest working Windows Python.
4. Stdlib / project tools allowed.
5. No try/except, no raise, no secrets, no placeholders, no markdown, no prose.
6. No comments or notes. Windows-only APIs/paths.

## Program Files index

Module: `desktop_ai_agent.core.folder_index`

On setup start, scans only:

- `X:\Program Files`
- `X:\Program Files (x86)`

for drives `C`, `D`, `E` (configurable in `config/setup.json` → `program_files_index`).

Writes:

- `config/program_files_index.json`
- `config/program_files_index.txt` (lines like `steam, (C:\...\steam.exe)`)

Agent-facing API (never raises; empty string / `[]` on miss):

```python
from desktop_ai_agent.core.folder_index import resolve_path, search_programs, list_programs

resolve_path("steam")       # path or ""
search_programs("git")      # top matches [{name, path}, ...]
list_programs()             # full catalog
```

## Response normalization

Module: `desktop_ai_agent.core.response_format`

- Enforces JSON object with `script` + `code`
- Replaces filler job ids (`short`, `task`, …) using `title_from_user(task)`
- Strips markdown fences when present

## Config

| File | Purpose |
|------|---------|
| `config/setup.json` | Ollama host/models, index drives, UI |
| `config/Modelfile.gemma4-agent` | Agent system prompt + Ollama params |

## Manual smoke tasks

**Easy:** `print hello` · `open youtube` · `get budapest weather`  
**Medium:** `run vscode` · `open steam` · `create a file on desktop named test.txt with hi`  
**Hard:** `find git and print its path` · `list all indexed programs that contain steam or docker`

## Next (not done)

- Action Layer: parse JSON → `exec(code)` → return stdout/stderr to Core on failure
- Orchestrator: replace raw UI text with a structured task packet
- Input: voice / vision / gestures
