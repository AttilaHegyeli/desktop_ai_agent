# Desktop AI Agent — Ollama Gemma4 Core

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
├── app/                         # python -m app entry
├── src/desktop_ai_agent/
│   ├── core/                    # ollama, session, chat, folder_index
│   └── ui/                      # setup log + terminal
├── config/                      # setup.json + Modelfile
├── docs/                        # architecture + core + testing
└── tests/                       # pytest
```

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
