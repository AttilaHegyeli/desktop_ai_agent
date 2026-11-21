"""Start a Cloudflare quick tunnel and report its public https URL.

A quick tunnel needs no account, but its ``*.trycloudflare.com`` address is new on
every start. Requires ``winget install --id Cloudflare.cloudflared``.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable

_URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com", re.IGNORECASE)
_CANDIDATES = (
    r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe",
    r"%ProgramFiles%\cloudflared\cloudflared.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe",
)

UrlFn = Callable[["str | None"], None]
LogFn = Callable[[str], None]


def extract_url(line: str) -> str | None:
    """The tunnel's public URL in a cloudflared log line (ignores api.trycloudflare.com)."""
    for m in _URL_RE.finditer(line):
        if m.group(1).lower() != "api":
            return m.group(0).lower()
    return None


def find_cloudflared(configured: str | None = None) -> str | None:
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("cloudflared")
    if found:
        return found
    for template in _CANDIDATES:
        path = os.path.expandvars(template)
        if os.path.isfile(path):
            return path
    return None


class TunnelManager:
    def __init__(
        self,
        port: int,
        *,
        on_url: UrlFn,
        on_log: LogFn,
        exe: str | None = None,
        popen=subprocess.Popen,
    ) -> None:
        self.port = port
        self._on_url = on_url
        self._on_log = on_log
        self._exe = exe
        self._popen = popen
        self._proc: subprocess.Popen | None = None
        self._stopping = False
        self.url: str | None = None
        atexit.register(self.stop)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        exe = find_cloudflared(self._exe)
        if exe is None:
            self._on_log(
                "[tunnel] cloudflared not found. Install: winget install --id Cloudflare.cloudflared"
            )
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            self._proc = self._popen(
                [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            self._on_log(f"[tunnel] could not start cloudflared: {exc}")
            return False
        self._stopping = False
        proc = self._proc
        threading.Thread(
            target=self._read, args=(proc,), daemon=True, name="tunnel-reader"
        ).start()
        return True

    def stop(self) -> None:
        self._stopping = True
        proc, self._proc = self._proc, None
        self.url = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def _read(self, proc) -> None:
        try:
            for line in proc.stdout:
                url = extract_url(line)
                if url and url != self.url:
                    self.url = url
                    self._on_url(url)
                elif " ERR " in line:
                    self._on_log("[tunnel] " + line.strip()[:200])
        except Exception:  # noqa: BLE001
            pass
        if not self._stopping:
            self.url = None
            self._on_url(None)
            self._on_log(f"[tunnel] cloudflared exited (code {proc.poll()})")
