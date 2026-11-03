from __future__ import annotations

import logging

from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog

logger = logging.getLogger(__name__)

_catalog: ProgramCatalog | None = None


def set_catalog(catalog: ProgramCatalog | None) -> None:
    """Bind the active program catalog used by agent-facing helpers."""
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
    except Exception:
        logger.exception("Failed to load program catalog")
        return ProgramCatalog([])


def list_programs() -> list[dict[str, str]]:
    """Return every indexed executable entry: [{name, path}, ...]. Never raises."""
    try:
        return get_catalog().list_all()
    except Exception:
        logger.exception("list_programs failed")
        return []


def search_programs(query: str, limit: int = 3) -> list[dict[str, str]]:
    """
    Search executable index. Short list only (default top 3). Never raises.

    Example:
        search_programs("steam")
        -> [{"name": "steam", "path": "C:\\\\Program Files (x86)\\\\Steam\\\\steam.exe"}]
    """
    try:
        return get_catalog().search(str(query or ""), limit=max(1, int(limit)))
    except Exception:
        logger.exception("search_programs failed for query=%r", query)
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
        target = hit.get("path") or hit.get("aumid") or ""
        return f"{hit['name']}, ({target})"
    except Exception:
        logger.exception("resolve_program failed for query=%r", query)
        return ""


def resolve_path(query: str) -> str:
    """Best executable path / AUMID, or empty string. Never raises."""
    try:
        hits = search_programs(query, limit=1)
        if not hits:
            return ""
        return hits[0].get("path") or hits[0].get("aumid") or ""
    except Exception:
        logger.exception("resolve_path failed for query=%r", query)
        return ""


def programs_as_text() -> str:
    """Full exe index in the compact line format: `name, (path)`. Never raises."""
    try:
        return get_catalog().as_text()
    except Exception:
        logger.exception("programs_as_text failed")
        return ""


def tool_usage_hint() -> str:
    """Short text for setup log."""
    return (
        "Index tool ready: search_programs / open_app "
        "(from desktop_ai_agent.core.folder_index)"
    )
