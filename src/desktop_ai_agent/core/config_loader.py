from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from desktop_ai_agent.paths import SETUP_JSON


def load_setup(path: Any = None) -> dict[str, Any]:
    target = path or SETUP_JSON
    with open(target, encoding="utf-8") as fh:
        return json.load(fh)


def response_schema_example(setup: dict[str, Any] | None = None) -> dict[str, Any]:
    data = setup or load_setup()
    return deepcopy(data.get("formats", {}).get("example", {}))
