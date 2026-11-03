"""Ensure runtime packages are installed every time the app starts.

The mentor workflow: no separate ``requirements.txt`` dance — ``python -m app``
(and ``scripts/start.*``) always pip-installs what the demo needs, then launches.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

# Keep in sync with [project] dependencies + runtime extras in pyproject.toml
# (voice, screenshot, ui, tts). Dev/pytest stays optional and is not auto-installed.
RUNTIME_PACKAGES: tuple[str, ...] = (
    "Pillow>=10.0",
    "pymupdf>=1.24",
    "ddgs>=9.0",
    "faster-whisper>=1.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "mss>=9.0",
    "comtypes>=1.4",
    "pyttsx3>=2.90",
    "edge-tts>=6.1",
    "pygame>=2.5",
)

LogFn = Callable[[str], None]


def ensure_dependencies(log: LogFn | None = None) -> None:
    """Install / refresh runtime packages with the active interpreter's pip."""
    emit = log or (lambda _m: None)
    emit("Installing runtime dependencies (startup)…")
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-q",
        *RUNTIME_PACKAGES,
    ]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Could not install runtime dependencies. "
            f"Check network / pip, then retry. pip exit code: {exc.returncode}"
        ) from exc
    emit("Runtime dependencies ready.")
