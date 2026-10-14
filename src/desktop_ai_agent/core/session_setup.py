from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.folder_index import ProgramCatalog
from desktop_ai_agent.core.folder_index.indexer import index_program_files
from desktop_ai_agent.core.folder_index.tool import set_catalog, tool_usage_hint
from desktop_ai_agent.core.ollama_service import OllamaService
from desktop_ai_agent.core.subprocess_io import run_text
from desktop_ai_agent.paths import MODELFILE, PROGRAM_FILES_INDEX_TXT


LogFn = Callable[[str], None]


class SessionSetup:
    """
    Full pipeline driven only by `python -m app`:
    1) index Program Files on C/D/E
    2) ensure Ollama daemon
    3) auto-pull base model if missing
    4) create/update gemma4-agent profile
    5) apply /set commands
    6) ready for in-app terminal chat
    """

    def __init__(self, setup: dict[str, Any], service: OllamaService | None = None) -> None:
        self.setup = setup
        ollama_cfg = setup.get("ollama", {})
        self.base_model = ollama_cfg.get("base_model", "gemma4")
        self.agent_model = ollama_cfg.get("agent_model", "gemma4-agent")
        self.commands: list[str] = list(ollama_cfg.get("interactive_commands", []))
        self.host = ollama_cfg.get("host", "http://127.0.0.1:11434")
        index_cfg = setup.get("program_files_index", {})
        self.index_enabled = bool(index_cfg.get("enabled", True))
        self.index_resolve_exe = bool(index_cfg.get("resolve_exe", True))
        drives = index_cfg.get("drives") or ["C", "D", "E"]
        self.index_drives = tuple(str(d).rstrip(":").upper() for d in drives)
        self.service = service or OllamaService(
            host=self.host,
            timeout_sec=int(ollama_cfg.get("startup_timeout_sec", 60)),
        )
        self.chat: ChatClient | None = None
        self.program_catalog: ProgramCatalog | None = None

    def prepare(self, log: LogFn | None = None) -> ChatClient:
        emit = log or (lambda _m: None)
        if not self.service.is_available():
            raise RuntimeError(
                "Ollama is not installed or not on PATH. Install Ollama, then run: python -m app"
            )

        self._build_program_index(log=emit)

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
        )
        emit("Agent ready — chat terminal unlocked (nothink + JSON).")
        return self.chat

    def _build_program_index(self, log: LogFn | None = None) -> None:
        emit = log or (lambda _m: None)
        if not self.index_enabled:
            emit("Program Files index skipped (disabled in setup.json).")
            return

        drive_label = "/".join(self.index_drives)
        emit(f"Indexing Program Files on {drive_label} ...")
        try:
            catalog = ProgramCatalog(
                index_program_files(
                    drives=self.index_drives,
                    resolve_exe=self.index_resolve_exe,
                )
            )
            catalog.save()
            set_catalog(catalog)
            self.program_catalog = catalog
            emit(
                f"Program Files index ready: {len(catalog)} apps -> {PROGRAM_FILES_INDEX_TXT.name}"
            )
            emit(tool_usage_hint().splitlines()[0])
        except Exception as exc:  # noqa: BLE001 — setup continues with empty catalog
            set_catalog(ProgramCatalog([]))
            self.program_catalog = ProgramCatalog([])
            emit(f"Program Files index skipped (safe): {exc}")

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
