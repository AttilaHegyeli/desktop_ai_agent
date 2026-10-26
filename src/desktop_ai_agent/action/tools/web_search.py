"""Web text search via ddgs (DuckDuckGo / metasearch)."""
from __future__ import annotations

from typing import Any, Callable

_INSTALL_HINT = "pip install -e ."
_DEFAULT_MAX_RESULTS = 3
_MAX_RESULTS_CAP = 5


def search_web(
    query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
    *,
    search_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run a text search and return ``{query, results}``.

    Each result is ``{title, url, snippet}``. Raises ``ValueError`` for empty
    queries and ``RuntimeError`` / ``ImportError`` on client failures.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query must not be empty")

    limit = int(max_results)
    if limit < 1:
        limit = 1
    if limit > _MAX_RESULTS_CAP:
        limit = _MAX_RESULTS_CAP

    raw = _run_search(q, limit, search_fn=search_fn)
    results = [_normalize_hit(item) for item in raw]
    results = [r for r in results if r["title"] or r["url"] or r["snippet"]]
    return {"query": q, "results": results[:limit]}


def _run_search(
    query: str,
    max_results: int,
    *,
    search_fn: Callable[..., list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if search_fn is not None:
        return list(search_fn(query, max_results=max_results) or [])

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ImportError(
            f"web_search requires ddgs. Install with: {_INSTALL_HINT}"
        ) from exc

    try:
        return list(DDGS().text(query, max_results=max_results) or [])
    except Exception as exc:  # network / backend errors from ddgs
        raise RuntimeError(f"web search failed: {exc}") from exc


def _normalize_hit(item: dict[str, Any]) -> dict[str, str]:
    title = str(item.get("title") or "").strip()
    url = str(item.get("href") or item.get("url") or "").strip()
    snippet = str(item.get("body") or item.get("snippet") or "").strip()
    return {"title": title, "url": url, "snippet": snippet}
