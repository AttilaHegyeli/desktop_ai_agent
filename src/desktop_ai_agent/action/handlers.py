"""Handlers: the code that actually does things.

RULES
- Only executor.py may import this module. Nothing else decides what runs.
- No safety logic here. Validation, risk and confirmation are the gate's job.
  (open_app below has its own input checks: it is picking a file to launch.)
- Signature: fn(args: dict) -> JSON-serialisable result. Raise on failure.
- Arguments arrive already validated against the registry.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from desktop_ai_agent.action.tools.screenshot import capture_screen
from desktop_ai_agent.action.tools.web_open import open_url as _open_url
from desktop_ai_agent.action.tools.web_search import search_web as _search_web


def wait(args: dict[str, Any]) -> dict[str, Any]:
    time.sleep(args["seconds"])
    return {"waited_seconds": args["seconds"]}


def screenshot(args: dict[str, Any]) -> dict[str, Any]:
    return capture_screen()


def provide_script(args: dict[str, Any]) -> dict[str, str]:
    """Display-only: hands the script back as data. It is NEVER executed."""
    return {"script": args["script"], "code": args["code"]}


def web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch web snippets for the model to answer from. Does not open a browser."""
    max_results = args.get("max_results", 3)
    return _search_web(args["query"], max_results=max_results)


def web_open(args: dict[str, Any]) -> dict[str, Any]:
    """Open an http(s) URL in the user's default browser."""
    return _open_url(args["url"])


# ---- open_app ----------------------------------------------------------------
# The three helpers below are the only places that touch the folder index or the
# OS, so tests can replace them and no real program is ever launched.

def _search_programs(query: str, limit: int) -> list[dict[str, str]]:
    # Imported lazily and called on every request: it always reads the catalog that
    # is active right now. To inject the catalog instead, only this helper changes.
    from desktop_ai_agent.core.folder_index import search_programs

    return search_programs(query, limit=limit)


def _normalize_name(raw: str) -> str:
    from desktop_ai_agent.core.folder_index.indexer import normalize_name

    return normalize_name(raw)


def _start_file(path: str) -> None:
    if not hasattr(os, "startfile"):
        raise RuntimeError("open_app is only supported on Windows")
    os.startfile(path)  # no arguments are ever passed to the program


def _launch_aumid(aumid: str) -> None:
    from desktop_ai_agent.core.folder_index.appx import launch_aumid

    launch_aumid(aumid)


def _is_launchable_exe(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    return p.suffix.lower() == ".exe" and p.is_file()


def _is_launchable_hit(hit: dict[str, str]) -> bool:
    if hit.get("aumid"):
        return True
    return _is_launchable_exe(hit.get("path") or "")


def _name_matches(hit: dict[str, str], wanted: str, wanted_c: str) -> bool:
    name = hit.get("name") or ""
    if name == wanted or name.replace("_", "") == wanted_c:
        return True
    aliases = hit.get("aliases") or ""
    parts = [a.strip() for a in aliases.split(",") if a.strip()] if isinstance(aliases, str) else list(aliases)
    for alias in parts:
        if alias == wanted or alias.replace("_", "") == wanted_c:
            return True
    return False


def open_app(args: dict[str, Any]) -> dict[str, str]:
    """Launch an indexed program by name. The path/AUMID always comes from the index."""
    query = args["name"].strip()
    if not query:
        raise ValueError("name must not be empty")

    hits = _search_programs(query, 5)
    if not hits:
        raise LookupError(f"No installed program matches '{query}'.")

    launchable = [h for h in hits if _is_launchable_hit(h)]
    if not launchable:
        found = ", ".join(h["name"] for h in hits)
        raise ValueError(
            f"Found {found} for '{query}', but none has a launchable .exe/AUMID in the index."
        )

    wanted = _normalize_name(query)
    wanted_c = wanted.replace("_", "")
    exact = [h for h in launchable if _name_matches(h, wanted, wanted_c)]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(launchable) == 1:
        chosen = launchable[0]
    else:
        details = ", ".join(
            f"{h['name']} ({h.get('path') or h.get('aumid') or '?'})" for h in launchable
        )
        raise ValueError(
            f"Several programs match '{query}': {details}. Ask the user which one to open."
        )

    aumid = chosen.get("aumid") or ""
    if aumid:
        _launch_aumid(aumid)
        return {"opened": chosen["name"], "path": "", "aumid": aumid}

    path = chosen["path"]
    _start_file(path)
    return {"opened": chosen["name"], "path": path}


# action name -> handler. Must match the registry exactly (the executor checks).
HANDLERS = {
    "screenshot": screenshot,
    "wait": wait,
    "provide_script": provide_script,
    "open_app": open_app,
    "web_search": web_search,
    "web_open": web_open,
}
