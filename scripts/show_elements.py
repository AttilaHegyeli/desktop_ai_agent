"""Print the element list the model gets for the window in front (a dev script).

Start it, click the browser within the delay, then read the list in the terminal.
It calls the real list_elements("active"): the agent's browser gives the page list,
the user's own browser is handed over to the agent's browser first (opens the page
there), any other window gives the UIA list.

Usage (project root):
  python scripts/show_elements.py [--delay 2] [--request "words"] [--area active]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from desktop_ai_agent.action.tools import ui_control  # noqa: E402
from desktop_ai_agent.input.page_elements import estimate_tokens  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delay", type=float, default=2.0, help="seconds to wait (default 2)")
    parser.add_argument("--request", default="", help="user words; matching elements are listed first")
    parser.add_argument("--area", default="active", choices=ui_control.AREAS)
    args = parser.parse_args()

    print(f"Click the browser now... listing in {args.delay:g} s")
    time.sleep(args.delay)

    started = time.perf_counter()
    try:
        result = ui_control.list_elements(args.area, request=args.request)
    except Exception as exc:  # noqa: BLE001 - dev script: show the error and stop
        print(f"Error: {type(exc).__name__}: {exc}")
        return 1
    seconds = time.perf_counter() - started

    elements = result.pop("elements", "")
    for key, value in result.items():
        print(f"{key}: {value}")
    print("-" * 60)
    print(elements or "(no elements)")
    print("-" * 60)
    print(f"{len(elements)} chars, ~{estimate_tokens(elements)} tokens, {seconds:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
