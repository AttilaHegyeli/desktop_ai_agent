"""Screen capture: grab the desktop (or a region) and save a PNG under logs/screenshots/."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from desktop_ai_agent.paths import SCREENSHOTS_DIR

_INSTALL_HINT = 'pip install -e ".[screenshot]"'
_SCOPES = frozenset({"primary", "all", "monitor", "region"})


def capture_screen(
    directory: Path | None = None,
    *,
    scope: str = "primary",
    monitor: int | None = None,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Capture the screen (or a region) and write a PNG.

    ``scope``:
      - ``primary`` — first physical monitor (mss index 1); default
      - ``all`` — full virtual desktop (mss index 0)
      - ``monitor`` — physical monitor ``monitor`` (1-based mss index)
      - ``region`` — rectangle ``(x, y, width, height)`` in virtual-desktop coords

    Returns ``{"path", "width", "height", "scope", ...}``.
    Raises ``ImportError`` if mss/Pillow are missing, ``ValueError`` for bad args,
    or ``OSError`` on I/O failure.
    """
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            f"screenshot requires mss and Pillow. Install with: {_INSTALL_HINT}"
        ) from exc

    if scope not in _SCOPES:
        raise ValueError(f"scope must be one of {sorted(_SCOPES)}, got '{scope}'")

    out_dir = Path(directory) if directory is not None else SCREENSHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        grab, meta = _resolve_target(
            sct.monitors,
            scope=scope,
            monitor=monitor,
            x=x,
            y=y,
            width=width,
            height=height,
        )
        shot = sct.grab(grab)
        img_w, img_h = shot.width, shot.height
        image = Image.frombytes("RGB", (img_w, img_h), shot.rgb)

    path = _unique_path(out_dir)
    image.save(path, format="PNG")
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "width": img_w,
        "height": img_h,
        "scope": scope,
    }
    result.update(meta)
    return result


def _resolve_target(
    monitors: list[dict[str, int]],
    *,
    scope: str,
    monitor: int | None,
    x: int | None,
    y: int | None,
    width: int | None,
    height: int | None,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Return (mss grab dict, extra result metadata)."""
    # monitors[0] = virtual desktop; monitors[1..] = physical displays
    n_physical = max(0, len(monitors) - 1)

    if scope == "all":
        return dict(monitors[0]), {}

    if scope == "primary":
        if n_physical < 1:
            raise ValueError("No physical monitor available to capture")
        return dict(monitors[1]), {"monitor": 1}

    if scope == "monitor":
        if monitor is None:
            raise ValueError("scope='monitor' requires parameter 'monitor' (>= 1)")
        if monitor < 1 or monitor > n_physical:
            raise ValueError(
                f"monitor must be between 1 and {n_physical}, got {monitor}"
            )
        return dict(monitors[monitor]), {"monitor": monitor}

    # scope == "region"
    missing = [
        name
        for name, val in (("x", x), ("y", y), ("width", width), ("height", height))
        if val is None
    ]
    if missing:
        raise ValueError(
            f"scope='region' requires parameters {missing} "
            "(x, y, width, height in virtual-desktop coordinates)"
        )
    assert x is not None and y is not None and width is not None and height is not None
    if width < 1 or height < 1:
        raise ValueError(f"region width and height must be >= 1, got {width}x{height}")
    return (
        {"left": x, "top": y, "width": width, "height": height},
        {"x": x, "y": y, "region_width": width, "region_height": height},
    )


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
