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

from desktop_ai_agent.action.tools import page_control as _page_control
from desktop_ai_agent.action.tools import ui_control as _ui_control
from desktop_ai_agent.action.tools import window_control as _window_control
from desktop_ai_agent.action.tools import window_wait as _window_wait
from desktop_ai_agent.action.tools.screenshot import capture_screen
from desktop_ai_agent.action.tools.web_search import search_web as _search_web


def wait(args: dict[str, Any]) -> dict[str, Any]:
    time.sleep(args["seconds"])
    return {"waited_seconds": args["seconds"]}


def screenshot(args: dict[str, Any]) -> dict[str, Any]:
    return capture_screen(
        scope=args.get("scope", "primary"),
        monitor=args.get("monitor"),
        x=args.get("x"),
        y=args.get("y"),
        width=args.get("width"),
        height=args.get("height"),
    )


def provide_script(args: dict[str, Any]) -> dict[str, str]:
    """Display-only: hands the script back as data. It is NEVER executed."""
    return {"script": args["script"], "code": args["code"]}


def web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch web snippets for the model to answer from. Does not open a browser."""
    max_results = args.get("max_results", 3)
    return _search_web(args["query"], max_results=max_results)


def web_open(args: dict[str, Any]) -> dict[str, Any]:
    """Open an http(s) URL in a new tab of the agent's own browser (it waits for the page)."""
    return _open_in_browser(args["url"])


def _open_in_browser(url: str) -> dict[str, Any]:
    # Seam: tests replace it, so no real browser is ever started.
    return _page_control.open_url(url)


def list_elements(args: dict[str, Any]) -> dict[str, Any]:
    """Numbered clickable elements of the screen area or the web page, matches to the request first."""
    return _ui_control.list_elements(
        args.get("area", "active"), args.get("request", ""), args.get("cloud", False)
    )


def browser_read(args: dict[str, Any]) -> dict[str, Any]:
    """Text of the web page in front (handed over from the user's browser) or the agent's tab."""
    return _page_control.read(
        args.get("find"),
        args.get("part", 1),
        args.get("request", ""),
        args.get("cloud", False),
        _ui_control.user_browser_in_front(),
    )


def browser_go(args: dict[str, Any]) -> dict[str, Any]:
    """Back / forward / reload in the agent's current tab. Old element numbers are stale after it."""
    result = _page_control.go(args["action"])
    _ui_control.clear_list_after_action("browser_go")
    return result


def list_windows(args: dict[str, Any]) -> dict[str, Any]:
    """Numbered open windows, front first. Our own app is left out."""
    return _window_control.list_windows()


def window_control(args: dict[str, Any]) -> dict[str, Any]:
    """Focus / minimize / maximize / restore / close one window, by name or number."""
    return _window_control.window_control(args["action"], args.get("name"), args.get("number"))


def move_mouse(args: dict[str, Any]) -> dict[str, Any]:
    """Pointer onto a listed element's centre. Never clicks."""
    return _ui_control.move_mouse(args["element"])


def click(args: dict[str, Any]) -> dict[str, Any]:
    """Pointer onto a listed element's centre, then one click (inside the page for web elements)."""
    return _ui_control.click(
        args["element"], args.get("button", "left"), args.get("double", False), args.get("request", "")
    )


def type_text(args: dict[str, Any]) -> dict[str, Any]:
    """Enter text into a listed input field. Enter only with submit, in web search fields."""
    return _ui_control.type_text(args["element"], args["text"], args.get("submit", False))


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


def open_app(args: dict[str, Any]) -> dict[str, Any]:
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
        def launch() -> dict[str, Any]:
            _launch_aumid(aumid)
            return {"opened": chosen["name"], "path": "", "aumid": aumid}

        return _with_app_window(chosen["name"], "", launch)  # Store app: no own .exe to match

    path = chosen["path"]

    def launch() -> dict[str, Any]:
        _start_file(path)
        return {"opened": chosen["name"], "path": path}

    return _with_app_window(chosen["name"], Path(path).stem, launch)


def _with_app_window(name: str, exe_stem: str, launch: Any) -> dict[str, Any]:
    # Seam: tests replace it, so no real window is ever enumerated or focused.
    return _window_wait.open_app_window(name, exe_stem, launch)


# action name -> handler. Must match the registry exactly (the executor checks).
HANDLERS = {
    "screenshot": screenshot,
    "wait": wait,
    "provide_script": provide_script,
    "open_app": open_app,
    "web_search": web_search,
    "web_open": web_open,
    "list_elements": list_elements,
    "browser_read": browser_read,
    "browser_go": browser_go,
    "list_windows": list_windows,
    "window_control": window_control,
    "move_mouse": move_mouse,
    "click": click,
    "type_text": type_text,
}
