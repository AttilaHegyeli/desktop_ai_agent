# Desktop AI Agent — Ollama Gemma4 Setup

One command:

```powershell
python -m app
```

Opens a setup UI with an **embedded terminal**. Auto: start Ollama → pull `gemma4` if needed → build `gemma4-agent` → `/set nothink` + JSON → chat in the same window.

## Layout

```
desktop_ai_agent/
├── app/                      # thin entry for `python -m app`
├── src/desktop_ai_agent/
│   ├── core/                 # ollama, session, chat API
│   └── ui/                   # setup log + terminal
├── config/                   # setup.json + Modelfile
├── docs/
└── scripts/
```

## Prerequisite

[Ollama](https://ollama.com) on PATH.

## Config

- `config/setup.json`
- `config/Modelfile.gemma4-agent`

Expected reply:

```json
{ "script": "print hello", "code": "print('hello')\n" }
```
