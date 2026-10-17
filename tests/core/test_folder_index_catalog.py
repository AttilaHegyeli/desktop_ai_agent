from __future__ import annotations

import json
from pathlib import Path

from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog


def test_search_ranks_exact_first(sample_entries: list[dict[str, str]]) -> None:
    catalog = ProgramCatalog(sample_entries)
    hits = catalog.search("steam", limit=3)
    assert hits
    assert hits[0]["name"] == "steam"
    assert hits[0]["path"].lower().endswith("steam.exe")


def test_search_empty_query() -> None:
    catalog = ProgramCatalog([{"name": "git", "display": "Git", "path": "x"}])
    assert catalog.search("") == []


def test_search_miss_returns_empty(sample_entries: list[dict[str, str]]) -> None:
    assert ProgramCatalog(sample_entries).search("no_such_app_zzz") == []


def test_save_and_load_roundtrip(
    sample_entries: list[dict[str, str]], tmp_path: Path
) -> None:
    catalog = ProgramCatalog(sample_entries)
    json_path = tmp_path / "index.json"
    txt_path = tmp_path / "index.txt"
    catalog.save(json_path=json_path, txt_path=txt_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["count"] == 3
    assert "steam, (" in txt_path.read_text(encoding="utf-8")

    loaded = ProgramCatalog.load(json_path=json_path, rebuild_if_missing=False)
    assert len(loaded) == 3
    assert loaded.search("git")[0]["name"] == "git"


def test_load_missing_without_rebuild(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    catalog = ProgramCatalog.load(json_path=missing, rebuild_if_missing=False)
    assert len(catalog) == 0


def test_list_all_and_as_text(sample_entries: list[dict[str, str]]) -> None:
    catalog = ProgramCatalog(sample_entries)
    assert len(catalog.list_all()) == 3
    assert "docker, (" in catalog.as_text()
