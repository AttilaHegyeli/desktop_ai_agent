from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.ollama_service import OllamaService
from desktop_ai_agent.core.subprocess_io import run_text
from desktop_ai_agent.paths import MODELFILE


LogFn = Callable[[str], None]


class SessionSetup:
    """
    Full pipeline driven only by `python -m app`:
    1) ensure Ollama daemon
    2) auto-pull base model if missing
    3) create/update gemma4-agent profile
    4) apply /set commands
    5) ready for in-app terminal chat
    """

    def __init__(self, setup: dict[str, Any], service: OllamaService | None = None) -> None:
        self.setup = setup
        ollama_cfg = setup.get("ollama", {})
        self.base_model = ollama_cfg.get("base_model", "gemma4")
        self.agent_model = ollama_cfg.get("agent_model", "gemma4-agent")
        self.commands: list[str] = list(ollama_cfg.get("interactive_commands", []))
        self.host = ollama_cfg.get("host", "http://127.0.0.1:11434")
        self.service = service or OllamaService(
            host=self.host,
            timeout_sec=int(ollama_cfg.get("startup_timeout_sec", 60)),
        )
        self.chat: ChatClient | None = None

    def prepare(self, log: LogFn | None = None) -> ChatClient:
        emit = log or (lambda _m: None)
        if not self.service.is_available():
            raise RuntimeError(
                "Ollama is not installed or not on PATH. Install Ollama, then run: python -m app"
            )

        self.service.ensure_running(log=emit)
        self.service.ensure_model(self.base_model, log=emit)

        if not MODELFILE.exists():
            raise FileNotFoundError(f"Missing Modelfile: {MODELFILE}")

        self.service.create_from_modelfile(self.agent_model, str(MODELFILE), log=emit)
        self.apply_session_commands(log=emit)

        self.chat = ChatClient(
            model=self.agent_model,
            host=self.host,
            think=False,
            response_format="json",
        )
        emit("Agent ready — chat terminal unlocked (nothink + JSON).")
        return self.chat

    def apply_session_commands(self, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if not self.commands:
            return
        if not self.service.binary:
            raise RuntimeError("ollama executable not found on PATH.")

        emit("Applying session commands: " + " | ".join(self.commands))
        payload = "\n".join([*self.commands, "/bye"]) + "\n"
        result = run_text(
            [self.service.binary, "run", self.agent_model],
            input=payload,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            emit(
                f"WARNING: /set apply returned {result.returncode}: "
                f"{detail.splitlines()[-1] if detail else ''}"
            )
        else:
            emit("Session /set commands applied.")

    def run_full_open(self, log: LogFn | None = None) -> ChatClient:
        return self.prepare(log=log)
