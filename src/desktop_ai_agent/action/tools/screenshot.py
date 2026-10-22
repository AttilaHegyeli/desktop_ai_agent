"""Screen capture: grab the desktop and save a PNG under logs/screenshots/."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from desktop_ai_agent.paths import SCREENSHOTS_DIR

_INSTALL_HINT = 'pip install -e ".[screenshot]"'


def capture_screen(directory: Path | None = None) -> dict[str, Any]:
    """Capture the full virtual desktop and write a PNG.

    Returns ``{"path": str, "width": int, "height": int}``.
    Raises ``ImportError`` if mss/Pillow are missing, or ``OSError`` on I/O failure.
    """
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            f"screenshot requires mss and Pillow. Install with: {_INSTALL_HINT}"
        ) from exc

    out_dir = Path(directory) if directory is not None else SCREENSHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        # monitor 0 = full virtual desktop (all monitors combined)
        shot = sct.grab(sct.monitors[0])
        width, height = shot.width, shot.height
        image = Image.frombytes("RGB", (width, height), shot.rgb)

    path = _unique_path(out_dir)
    image.save(path, format="PNG")
    return {"path": str(path.resolve()), "width": width, "height": height}


def _unique_path(directory: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = directory / f"{stamp}.png"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = directory / f"{stamp}_{n}.png"
        if not candidate.exists():
            return candidate
        n += 1
