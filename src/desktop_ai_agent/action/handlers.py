"""Handlers: the code that actually does things.

RULES
- Only executor.py may import this module. Nothing else decides what runs.
- No safety logic here. Validation, risk and confirmation are the gate's job.
- Signature: fn(args: dict) -> JSON-serialisable result. Raise on failure.
- Arguments arrive already validated against the registry.
"""
from __future__ import annotations

import time
from typing import Any

from desktop_ai_agent.action.tools.screenshot import capture_screen


def wait(args: dict[str, Any]) -> dict[str, Any]:
    time.sleep(args["seconds"])
    return {"waited_seconds": args["seconds"]}


def screenshot(args: dict[str, Any]) -> dict[str, Any]:
    return capture_screen()


def provide_script(args: dict[str, Any]) -> dict[str, str]:
    """Display-only: hands the script back as data. It is NEVER executed."""
    return {"script": args["script"], "code": args["code"]}


# action name -> handler. Must match the registry exactly (the executor checks).
HANDLERS = {
    "screenshot": screenshot,
    "wait": wait,
    "provide_script": provide_script,
}