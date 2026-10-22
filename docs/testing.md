# Testing

## Run

```powershell
pip install -e ".[dev]"
pytest
```

Without editable install:

```powershell
pip install pytest
$env:PYTHONPATH = "src"
pytest
```

## Layout

```text
tests/
  conftest.py
  core/           # chat client, folder index, response format
  action/         # registry, executor, handlers, screenshot, open_app
  orchestrator/   # agent loop, prompts, service, vision heuristics
  ui/             # event formatting
  input/          # voice STT (mocked; no mic / model download)
  output/         # TTS helpers
```

## Notes

- Tests use **stdlib + pytest**. Voice unit tests mock faster-whisper / sounddevice.
- Program Files indexing uses a fake tree under `tmp_path` — CI does not need a real `C:\Program Files`.
- Install `[voice]` / `[screenshot]` / `[tts]` only when you exercise those features in the real app, not for the default unit suite.
