# Testing

## Run

```powershell
pip install -e ".[dev]"
pytest
```

Or without editable install:

```powershell
pip install pytest
$env:PYTHONPATH = "src"
pytest
```

## Layout

```text
tests/
  conftest.py
  core/
  action/
  orchestrator/
  ui/
  input/          # voice STT (mocked; no mic / model download)
```

Tests are stdlib + pytest only (voice unit tests mock faster-whisper / sounddevice). Program Files indexing uses a fake directory tree under `tmp_path` so CI does not need real `C:\Program Files`. Install voice runtime deps with `pip install -e ".[voice]"` when using the Mic button.
