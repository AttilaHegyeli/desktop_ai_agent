# Desktop AI Agent — Ollama Gemma4 Setup

One command:

```powershell
python -m app
```

Opens a setup UI with an embedded terminal. Auto: index Program Files → start Ollama → pull `gemma4` if needed → build `gemma4-agent` → chat through the action layer.

Optional extras:

```powershell
pip install -e ".[voice]"   # Mic + wake-word STT
pip install -e ".[tts]"     # speaker feedback (neural edge-tts + SAPI fallback)
```

## Layout

```text
desktop_ai_agent/
├── app/                          # thin bootstrap: python -m app
├── src/desktop_ai_agent/
│   ├── input/
│   │   └── voice.py              # Mic PTT + wake-word STT
│   ├── core/                     # ollama, session, chat, folder_index
│   ├── orchestrator/             # agent loop + service
│   ├── action/                   # registry, executor, handlers
│   ├── output/
│   │   └── tts.py                # speaker feedback (pyttsx3)
│   └── ui/
│       └── launcher.py           # setup log + terminal
├── config/                       # setup.json + Modelfile
├── docs/
├── tests/
└── pyproject.toml
```

## Prerequisite

[Ollama](https://ollama.com) on PATH.

## Docs

- [Architecture (target pipeline)](docs/architecture.md)
- [Quick guide (current state)](docs/guide_current_state.md)
- [Core AI](docs/core.md)
- [Testing](docs/testing.md)

## Tests

```powershell
pip install -e ".[dev]"
pytest
```

## Config

- `config/setup.json` — models, index drives, UI, `voice`, `output.tts`
- `config/Modelfile.gemma4-agent` — Core scriptwriter prompt
