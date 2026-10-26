# Core AI

Core is the **local brain**: Ollama + Gemma 4. It does not touch the OS. It talks to the orchestrator (messages + tools) and relies on the action layer for side effects.

## Role in the live pipeline

```text
UI / voice
  → AgentService / agent_loop
       → ChatClient (Ollama, gemma4) + tools from registry
       → Executor (action layer)
       → results back to the model
```

Startup (`SessionSetup.prepare()`):

1. Index usable application `.exe` files (drives from config).
2. Ensure Ollama is up; pull `gemma4` if missing.
3. Optionally build `gemma4-agent` from the Modelfile (legacy profile; chat uses base model + in-code system prompt).
4. Hand a ready `ChatClient` to the UI / orchestrator.

## Models

| Config key | Default | Use |
|------------|---------|-----|
| `ollama.base_model` | `gemma4` | Active chat + tool calling |
| `ollama.agent_model` | `gemma4-agent` | Built at setup; not used for the live tool loop |
| `ollama.host` | `http://127.0.0.1:11434` | Ollama API |

System instructions for the agent live in `orchestrator/prompts.py` (no model rebuild when you edit them). Tool definitions always come from the action registry.

## Folder index

Module: `desktop_ai_agent.core.folder_index`

On setup (config key `folder_index`, legacy `program_files_index` still read), indexes **usable application `.exe` files** (deduped by absolute path):

- Start Menu / Startup `.lnk` targets (in-process `.lnk` parse; keeps `link` + exe `path`)
- Windows Settings → Apps (Uninstall registry DisplayName / icon / install dir)
- Store / AppX apps via Shell:AppsFolder (`aumid` launch id)
- `X:\Program Files` / `Program Files (x86)` (primary per product; vendor umbrellas expand)
- `Games` / `games` folders (drive root and under `Users\*`)
- optional `apps.extra_roots`

Entries are keyed by launch target (path or AUMID); extra names become `aliases`.

Skips uninstallers, setup helpers, crash reporters, and redistributables.

`.lnk` targets are resolved in-process (MS-SHLLINK binary parse — no PowerShell per shortcut). AppX apps launch via `shell:AppsFolder\<AUMID>`.

Writes:

- `config/program_files_index.json` / `.txt`

Agent-facing helpers (never raise; empty / `[]` on miss):

```python
from desktop_ai_agent.core.folder_index import resolve_path, search_programs, list_programs

resolve_path("steam")       # exe path or ""
search_programs("git")      # top exe matches
list_programs()             # full catalog
```

`open_app` uses this catalog so the model never invents executable paths.

## Chat client

`core/chat_client.py` wraps Ollama chat (including multimodal image attachments after screenshots).

`core/response_format.py` still helps normalize legacy `{script, code}` JSON when needed; the primary path today is **native tool calls**, not free-form script JSON.

## Related modules

| Module | Job |
|--------|-----|
| `core/session_setup.py` | First-run prepare |
| `core/ollama_service.py` | Start / wait for Ollama |
| `core/modelfile.py` | Build custom Ollama models |
| `core/config_loader.py` | Load `config/setup.json` |
| `core/folder_index/` | Scan + search catalog |

## Smoke ideas (manual)

**Easy:** `print hello` · greetings · `wait 1 second`  
**Medium:** `open steam` · `open vs code` · `what do you see on my screen?`  
**Hard:** ask for something with no tool → accept a script → confirm it is shown only  

## See also

- [Action layer](action.md)  
- [Configuration](configuration.md)  
- [Demo & usage](demo.md)  
