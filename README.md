# Desktop AI Agent

**Demo v0.1.0** — a local multimodal desktop assistant.

You talk or type. A local **Gemma 4** model (via [Ollama](https://ollama.com)) decides what to do. A gated **action layer** runs only approved tools (open apps, web search/open, screenshot, wait, or show a script). Replies come back as text and optional speech.

```text
text / voice  →  input  →  orchestrator  →  gemma4 (core)  →  action layer  →  chat / voice
                                              ↑__________________|
```

![Architecture](docs/assets/architecture.png)

## What this demo includes

| Area | In v0.1.0 |
|------|-----------|
| **Chat** | Always-on-top top-edge bar (hover to expand; last me/ai for 20s) |
| **Voice in** | Mic push-to-talk + wake word (“hey Zsuzsi”) via faster-whisper |
| **Core AI** | Ollama + `gemma4`, tool calling, folder index (apps + files) |
| **Actions** | `open_app`, `web_search`, `web_open`, `screenshot`, `wait`, `provide_script` (display only) |
| **Voice out** | Optional TTS via edge-tts (or offline SAPI) |
| **Safety** | Registry + executor gate; high-risk actions denied until confirm UI exists |

Not in this demo yet: mouse/keyboard control, full browser automation, camera gestures, free `exec` of model code.

## Quick start

### Prerequisites

- Windows 10/11
- Python **3.10+**
- [Ollama](https://ollama.com) installed and on `PATH`

### Install and run

From the project root:

```powershell
pip install -e .
python -m app
```

Or double-click / run:

```powershell
scripts\start.bat
```

On first launch the UI will:

1. Index usable application `.exe` files on drives C/D/E 
2. Start Ollama and pull `gemma4` if needed  
3. Show the top-edge chat bar  

Hover the nub at the top of the screen, then type something like `open steam` or `what do you see on my screen?`.

### Optional extras

```powershell
pip install -e ".[voice]"       # Mic + wake-word STT
pip install -e ".[tts]"         # Speaker feedback (edge-tts / SAPI)
pip install -e ".[screenshot]"  # Screen capture for vision tasks
```

Voice and TTS are configured under `voice` and `output.tts` in [`config/setup.json`](config/setup.json).

## Project layout

```text
desktop_ai_agent/
├── app/                         # python -m app bootstrap
├── src/desktop_ai_agent/
│   ├── input/                   # voice STT (optional)
│   ├── core/                    # Ollama, session, folder index, chat
│   ├── orchestrator/            # agent loop + UI service
│   ├── action/                  # registry, executor, handlers
│   ├── output/                  # TTS (optional)
│   └── ui/                      # setup launcher
├── config/                      # setup.json, Modelfile, index output
├── docs/                        # detailed documentation
├── tests/
└── pyproject.toml
```

## Documentation

| Doc | Contents |
|-----|----------|
| [Architecture](docs/architecture.md) | Pipeline layers and how data flows |
| [Demo & usage](docs/demo.md) | How to use v0.1.0 day to day |
| [Core AI](docs/core.md) | Ollama, models, folder index |
| [Action layer](docs/action.md) | Tools, safety, how to add an action |
| [Configuration](docs/configuration.md) | `setup.json` reference |
| [Testing](docs/testing.md) | How to run tests |

## Tests

```powershell
pip install -e ".[dev]"
pytest
```

## License / status

Demo release **0.1.0**. Expect rough edges with a small local model; check `logs/audit.jsonl` when something looks wrong.
