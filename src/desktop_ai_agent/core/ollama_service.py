from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from desktop_ai_agent.core.modelfile import load_modelfile, strip_optional_parameters
from desktop_ai_agent.core.subprocess_io import popen_text, run_text


LogFn = Callable[[str], None]


class OllamaService:
    """Detect, start, and query the local Ollama daemon."""

    def __init__(self, host: str = "http://127.0.0.1:11434", timeout_sec: int = 60) -> None:
        self.host = host.rstrip("/")
        self.timeout_sec = timeout_sec
        self._serve_proc: subprocess.Popen[str] | None = None

    @property
    def binary(self) -> str | None:
        return shutil.which("ollama")

    def is_available(self) -> bool:
        return self.binary is not None

    def is_running(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def ensure_running(self, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if self.is_running():
            emit("Ollama already running.")
            return
        if not self.binary:
            raise RuntimeError("ollama executable not found on PATH.")

        emit("Starting ollama serve ...")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._serve_proc = popen_text(
            [self.binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            if self.is_running():
                emit("Ollama serve is ready.")
                return
            time.sleep(0.4)
        raise TimeoutError(f"Ollama did not become ready within {self.timeout_sec}s.")

    def list_models(self) -> list[str]:
        with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as resp:
            payload: dict[str, Any] = json.loads(resp.read().decode("utf-8", errors="replace"))
        return [m.get("name", "") for m in payload.get("models", [])]

    def has_model(self, name: str) -> bool:
        needle = name if ":" in name else f"{name}:latest"
        names = self.list_models()
        return any(n == name or n == needle or n.startswith(f"{name}:") for n in names)

    def ensure_model(self, name: str, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if self.has_model(name):
            emit(f"Base model '{name}' found.")
            return
        self.pull_model(name, log=emit)

    def pull_model(self, name: str, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if not self.binary:
            raise RuntimeError("ollama executable not found on PATH.")
        emit(f"Pulling '{name}' (first run may take a while) ...")
        proc = popen_text(
            [self.binary, "pull", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert proc.stdout is not None
        last_line = ""
        for line in proc.stdout:
            text = line.strip()
            if text and text != last_line:
                emit(text)
                last_line = text
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"ollama pull {name} failed (exit {code}).")
        if not self.has_model(name):
            raise RuntimeError(f"Model '{name}' still missing after pull.")
        emit(f"Pull complete: '{name}'.")

    def create_from_modelfile(self, model_name: str, modelfile_path: str, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if not self.binary:
            raise RuntimeError("ollama executable not found on PATH.")

        path = Path(modelfile_path)
        emit(f"Building model '{model_name}' from Modelfile ...")
        result = self._ollama_create(model_name, path)
        if result.returncode == 0:
            emit(f"Model '{model_name}' ready.")
            return

        detail = (result.stderr or result.stdout or "unknown error").strip()
        emit(
            f"Full Modelfile rejected ({detail.splitlines()[-1] if detail else 'error'}). "
            "Retrying without think/format params..."
        )
        fallback = strip_optional_parameters(load_modelfile(path))
        tmp = Path(tempfile.gettempdir()) / f"{model_name}.Modelfile.fallback"
        tmp.write_text(fallback, encoding="utf-8")
        result2 = self._ollama_create(model_name, tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if result2.returncode != 0:
            detail2 = (result2.stderr or result2.stdout or detail).strip()
            raise RuntimeError(f"ollama create failed: {detail2}")
        emit(f"Model '{model_name}' ready (compat Modelfile).")

    def _ollama_create(self, model_name: str, modelfile_path: Path) -> subprocess.CompletedProcess[str]:
        assert self.binary
        return run_text(
            [self.binary, "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            check=False,
        )
