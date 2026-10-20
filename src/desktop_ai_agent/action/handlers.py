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


def wait(args: dict[str, Any]) -> dict[str, Any]:
    time.sleep(args["seconds"])
    return {"waited_seconds": args["seconds"]}


def screenshot(args: dict[str, Any]) -> Any:
    # TODO: call the capture function that will live in input/ (not built yet).
    raise NotImplementedError("screenshot is not wired to input/ capture yet")


def provide_script(args: dict[str, Any]) -> dict[str, str]:
    """Display-only: hands the script back as data. It is NEVER executed."""
    return {"script": args["script"], "code": args["code"]}


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


def _is_launchable_exe(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() == ".exe" and p.is_file()


def open_app(args: dict[str, Any]) -> dict[str, str]:
    """Launch an indexed program by name. The path always comes from the index."""
    query = args["name"].strip()
    if not query:
        raise ValueError("name must not be empty")

    hits = _search_programs(query, 5)
    if not hits:
        raise LookupError(f"No installed program matches '{query}'.")

    launchable = [h for h in hits if _is_launchable_exe(h["path"])]
    if not launchable:
        found = ", ".join(h["name"] for h in hits)
        raise ValueError(
            f"Found {found} for '{query}', but none has a launchable .exe in the index."
        )

    wanted = _normalize_name(query)
    exact = [h for h in launchable if h["name"] == wanted]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(launchable) == 1:
        chosen = launchable[0]
    else:
        names = ", ".join(h["name"] for h in launchable)
        raise ValueError(
            f"Several programs match '{query}': {names}. Ask the user which one to open."
        )

    _start_file(chosen["path"])
    return {"opened": chosen["name"], "path": chosen["path"]}


# action name -> handler. Must match the registry exactly (the executor checks).
HANDLERS = {
    "screenshot": screenshot,
    "wait": wait,
    "provide_script": provide_script,
    "open_app": open_app,
}