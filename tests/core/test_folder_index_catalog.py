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


def test_search_compact_match_ignores_separators() -> None:
    """User says 'MongoDB Compass'; exe is MongoDBCompass.exe."""
    catalog = ProgramCatalog(
        [
            {
                "name": "mongod",
                "display": "mongod",
                "path": r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe",
            },
            {
                "name": "mongodbcompass",
                "display": "MongoDBCompass",
                "path": r"C:\Users\attil\AppData\Local\MongoDBCompass\MongoDBCompass.exe",
            },
        ]
    )
    hits = catalog.search("MongoDB Compass", limit=3)
    assert len(hits) == 1
    assert hits[0]["name"] == "mongodbcompass"


def test_search_similar_token_match() -> None:
    catalog = ProgramCatalog(
        [
            {
                "name": "mongodbcompass",
                "display": "MongoDBCompass",
                "path": r"C:\Users\x\MongoDBCompass.exe",
            }
        ]
    )
    hits = catalog.search("compass", limit=3)
    assert hits and hits[0]["name"] == "mongodbcompass"


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


def test_format_and_list_include_shortcut_link() -> None:
    from desktop_ai_agent.core.folder_index.indexer import format_index_lines

    entries = [
        {
            "name": "docker_desktop",
            "display": "Docker Desktop",
            "path": r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
            "link": r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Docker Desktop.lnk",
            "folder": r"C:\Program Files\Docker\Docker",
            "root": r"C:\Program Files\Docker",
        }
    ]
    text = format_index_lines(entries)
    assert "link=(" in text
    assert text.lower().endswith(".lnk)\n") or ".lnk)" in text

    catalog = ProgramCatalog(entries)
    row = catalog.list_all()[0]
    assert row["link"].lower().endswith(".lnk")


def test_search_matches_aliases() -> None:
    catalog = ProgramCatalog(
        [
            {
                "name": "vs_code",
                "display": "Visual Studio Code",
                "path": r"C:\Program Files\Microsoft VS Code\Code.exe",
                "aliases": ["vs_code", "code", "visual_studio_code"],
            }
        ]
    )
    hits = catalog.search("code", limit=3)
    assert hits and hits[0]["name"] == "vs_code"


def test_build_kwargs_from_setup_reads_folder_index() -> None:
    from desktop_ai_agent.core.folder_index.catalog import build_kwargs_from_setup

    kw = build_kwargs_from_setup(
        {
            "folder_index": {
                "drives": ["C", "D"],
                "apps": {
                    "include_start_menu": False,
                    "include_installed_apps": True,
                    "include_appx": False,
                    "include_games_folders": False,
                    "max_exe_depth": 2,
                    "max_exes": 100,
                },
            }
        }
    )
    assert kw["drives"] == ("C", "D")
    assert kw["include_start_menu"] is False
    assert kw["include_appx"] is False
    assert kw["max_exes"] == 100
