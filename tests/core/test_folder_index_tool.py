from __future__ import annotations

from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog
from desktop_ai_agent.core.folder_index import tool as tool_mod
from desktop_ai_agent.core.folder_index.tool import (
    list_programs,
    resolve_path,
    resolve_program,
    search_programs,
    set_catalog,
)


def test_resolve_helpers(sample_entries: list[dict[str, str]]) -> None:
    set_catalog(ProgramCatalog(sample_entries))
    assert resolve_path("steam").lower().endswith("steam.exe")
    assert resolve_program("git").startswith("git, (")
    assert resolve_path("missing_app_xyz") == ""
    assert resolve_program("missing_app_xyz") == ""
    assert search_programs("docker")[0]["name"] == "docker"
    assert len(list_programs()) == 3


def test_tools_never_raise_without_catalog() -> None:
    set_catalog(ProgramCatalog([]))
    assert resolve_path("steam") == ""
    assert search_programs("steam") == []
    assert list_programs() == []


def test_set_catalog_none_falls_back(monkeypatch) -> None:
    set_catalog(None)

    def boom(**_kwargs):
        raise RuntimeError("disk fail")

    monkeypatch.setattr(tool_mod.ProgramCatalog, "load", boom)
    assert resolve_path("steam") == ""
