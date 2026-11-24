"""Check an http(s) URL; open it in the system browser (legacy helper).

The agent's web_open opens pages in its OWN browser (``page_control.open_url``); it
uses ``check_url`` from here so both paths accept exactly the same URLs.
"""
from __future__ import annotations

import webbrowser
from typing import Any, Callable
from urllib.parse import urlparse


def check_url(url: str) -> str:
    """`url` stripped, or ValueError. Only ``http`` and ``https`` with a host are allowed."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("url must not be empty")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"url must start with http:// or https://, got scheme '{scheme or '(none)'}'"
        )
    if not parsed.netloc:
        raise ValueError("url must include a host")
    return raw


def open_url(
    url: str,
    *,
    open_fn: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Open ``url`` in the system browser. Only ``http`` and ``https`` are allowed."""
    raw = check_url(url)
    opener = open_fn if open_fn is not None else webbrowser.open
    opener(raw)
    return {"opened": raw}
