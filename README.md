<<<<<<< HEAD
# Desktop AI Agent — Ollama Gemma4 Core
=======
﻿# Desktop AI Agent — Ollama Gemma4 Setup
>>>>>>> origin/dev

One command:

```powershell
python -m app
```

Opens a setup UI with an embedded terminal. Auto: index Program Files → start Ollama → pull `gemma4` if needed → build `gemma4-agent` → chat. Core returns Action-ready JSON:

```json
{"script":"open_vs_code","code":"...windows python..."}
```

`code` is meant for a future Action Layer (`exec`). It is **not executed** by the UI yet.

## Layout

```text
desktop_ai_agent/
<<<<<<< HEAD
├── app/                         # python -m app entry
├── src/desktop_ai_agent/
│   ├── core/                    # ollama, session, chat, folder_index
│   └── ui/                      # setup log + terminal
├── config/                      # setup.json + Modelfile
├── docs/                        # architecture + core + testing
└── tests/                       # pytest
=======
├── app/                          # thin bootstrap: python -m app
│   └── __main__.py
│
├── src/desktop_ai_agent/
│   ├── __main__.py
│   ├── main.py                   # wires layers; no business logic
│   ├── paths.py
│   │
│   ├── input/                    # 1. Input layer (stub)
│   │   ├── voice.py              # STT (faster-whisper)
│   │   ├── vision.py             # camera / screen (OpenCV, mss)
│   │   └── gestures.py           # MediaPipe hands → coords
│   │
│   ├── orchestrator/             # 2. Orchestrator (stub)
│   │   ├── sync.py               # pair voice + gesture + screen
│   │   ├── filter.py             # noise / trigger gating
│   │   └── prompt_builder.py     # → structured prompt/JSON
│   │
│   ├── core/                     # 3. Core AI
│   │   ├── chat_client.py
│   │   ├── ollama_service.py
│   │   ├── session_setup.py
│   │   ├── modelfile.py
│   │   ├── config_loader.py
│   │   └── subprocess_io.py
│   │
│   ├── action/                   # 4. Action layer (stub)
│   │   ├── executor.py
│   │   ├── correction.py
│   │   └── tools/
│   │       ├── mouse_keyboard.py
│   │       ├── windows.py
│   │       └── apps.py
│   │
│   ├── output/                   # 5. Output / feedback (stub)
│   │   ├── tts.py
│   │   └── hud.py
│   │
│   └── ui/                       # human UI (setup + later chat/HUD)
│       ├── launcher.py
│       └── overlay.py
│
├── config/
│   ├── setup.json
│   └── Modelfile.gemma4-agent
│
├── docs/
│   └── architecture.md
│
├── scripts/
│   ├── start.bat
│   └── start.ps1
│
├── tests/                        # mirror packages (empty)
├── pyproject.toml
└── README.md
>>>>>>> origin/dev
```

`input/`, `orchestrator/`, `action/`, `output/`, and `tests/` are empty stubs for the layered pipeline described in `docs/architecture.md`. Only `core/` + `ui/launcher.py` are implemented today.

## Prerequisite

[Ollama](https://ollama.com) on PATH.

## Docs

- [Architecture (target pipeline)](docs/architecture.md)
- [Core AI (what works now)](docs/core.md)
- [Testing](docs/testing.md)

## Tests

```powershell
pip install -e ".[dev]"
pytest
```

## Config

- `config/setup.json` — models, index drives, UI
- `config/Modelfile.gemma4-agent` — Core scriptwriter prompt
