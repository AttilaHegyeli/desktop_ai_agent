from __future__ import annotations

from typing import Any

from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog

_catalog: ProgramCatalog | None = None


def set_catalog(catalog: ProgramCatalog | None) -> None:
    """Bind the active catalog used by agent-facing helpers."""
    global _catalog
    _catalog = catalog


def get_catalog(*, ensure_loaded: bool = True) -> ProgramCatalog:
    global _catalog
    try:
        if _catalog is None and ensure_loaded:
            _catalog = ProgramCatalog.load(rebuild_if_missing=True)
        if _catalog is None:
            return ProgramCatalog([])
        return _catalog
    except Exception:  # noqa: BLE001 — agent tool must never raise
        return ProgramCatalog([])


def list_programs() -> list[dict[str, str]]:
    """Return every indexed Program Files entry: [{name, path}, ...]. Never raises."""
    try:
        return get_catalog().list_all()
    except Exception:  # noqa: BLE001
        return []


def search_programs(query: str, limit: int = 3) -> list[dict[str, str]]:
    """
    Search Program Files index. Short list only (default top 3). Never raises.

    Example:
        search_programs("steam")
        -> [{"name": "steam", "path": "C:\\\\Program Files (x86)\\\\Steam\\\\steam.exe"}]
    """
    try:
        return get_catalog().search(str(query or ""), limit=max(1, int(limit)))
    except Exception:  # noqa: BLE001
        return []


def resolve_program(query: str) -> str:
    """
    Best single hit as compact text: `name, (path)`.
    Empty string if nothing found. Never raises.
    """
    try:
        hits = search_programs(query, limit=1)
        if not hits:
            return ""
        hit = hits[0]
        return f"{hit['name']}, ({hit['path']})"
    except Exception:  # noqa: BLE001
        return ""


def resolve_path(query: str) -> str:
    """Best path only, or empty string. Never raises."""
    try:
        hits = search_programs(query, limit=1)
        return hits[0]["path"] if hits else ""
    except Exception:  # noqa: BLE001
        return ""


def programs_as_text() -> str:
    """Full index in the compact line format: `name, (path)`. Never raises."""
    try:
        return get_catalog().as_text()
    except Exception:  # noqa: BLE001
        return ""


def tool_usage_hint() -> str:
    """Short text for setup log."""
    return (
        "Index tool ready: resolve_path / search_programs "
        "(from desktop_ai_agent.core.folder_index)"
    )


def catalog_snapshot_for_prompt(max_lines: int = 40) -> str:
    """Compact dump for system context (truncated). Never raises."""
    try:
        lines = get_catalog().as_text().splitlines()
        if len(lines) <= max_lines:
            body = "\n".join(lines)
        else:
            body = "\n".join(lines[:max_lines]) + f"\n... (+{len(lines) - max_lines}; use resolve_program)"
        return "Indexed programs:\n" + (body or "(empty)")
    except Exception:  # noqa: BLE001
        return "Indexed programs:\n(empty)"


def agent_system_rules() -> str:
    """Compat wrapper — canonical prompt lives in response_format."""
    from desktop_ai_agent.core.response_format import agent_system_prompt

    return agent_system_prompt()


def tool_search(query: str, **kwargs: Any) -> list[dict[str, str]]:
    return search_programs(query, **kwargs)
