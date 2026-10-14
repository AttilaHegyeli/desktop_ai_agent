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
    test_response_format.py
    test_folder_index_indexer.py
    test_folder_index_catalog.py
    test_folder_index_tool.py
```

Tests are stdlib + pytest only. Program Files indexing uses a fake directory tree under `tmp_path` so CI does not need real `C:\Program Files`.
