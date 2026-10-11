"""Bootstrap: put `src/` on sys.path, then start the real package."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    src_str = str(src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


_bootstrap()

from desktop_ai_agent.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
