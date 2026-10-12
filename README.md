# Desktop AI Agent — Ollama Gemma4 Setup

One command:

```powershell
python -m app
```

Opens a setup UI with an **embedded terminal**. Auto: start Ollama → pull `gemma4` if needed → build `gemma4-agent` → `/set nothink` + JSON → chat in the same window.

## Layout

```
desktop_ai_agent/
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
```

`input/`, `orchestrator/`, `action/`, `output/`, and `tests/` are empty stubs for the layered pipeline described in `docs/architecture.md`. Only `core/` + `ui/launcher.py` are implemented today.

## Prerequisite

[Ollama](https://ollama.com) on PATH.

## Config

- `config/setup.json`
- `config/Modelfile.gemma4-agent`

Expected reply:

```json
{ "script": "print hello", "code": "print('hello')\n" }
```
