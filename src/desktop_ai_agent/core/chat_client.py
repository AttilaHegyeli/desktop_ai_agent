from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from desktop_ai_agent.core.response_format import (
    AGENT_JSON_SCHEMA,
    agent_system_prompt,
    normalize_agent_reply,
)


LogFn = Callable[[str], None]


class ChatClient:
    """In-app Ollama chat via HTTP API (embedded terminal, no external console)."""

    def __init__(
        self,
        model: str,
        host: str = "http://127.0.0.1:11434",
        *,
        think: bool = False,
        response_format: str | dict[str, Any] | None = None,
        system: str | None = None,
        normalize: bool = True,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.think = think
        self.response_format: str | dict[str, Any] | None = (
            AGENT_JSON_SCHEMA if response_format is None else response_format
        )
        self.system = system if system is not None else agent_system_prompt()
        self.normalize = normalize
        self.messages: list[dict[str, str]] = []
        if self.system:
            self.messages.append({"role": "system", "content": self.system})

    def clear(self) -> None:
        self.messages.clear()
        if self.system:
            self.messages.append({"role": "system", "content": self.system})

    def ask(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        chunks: list[str] = []
        for piece in self._stream():
            chunks.append(piece)
        reply = self._finalize("".join(chunks), user_text)
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def ask_stream(self, user_text: str, on_chunk: Callable[[str], None]) -> str:
        """
        Stream tokens live, then replace with normalized JSON when done.
        on_chunk receives pieces; after stream, on_chunk may get a full cleaned body
        only via return value — caller should prefer the returned string.
        """
        self.messages.append({"role": "user", "content": user_text})
        chunks: list[str] = []
        for piece in self._stream():
            chunks.append(piece)
            on_chunk(piece)
        reply = self._finalize("".join(chunks), user_text)
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def _finalize(self, raw: str, user_text: str = "") -> str:
        if not self.normalize:
            return raw
        return normalize_agent_reply(raw, user_text=user_text)

    def _stream(self) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "stream": True,
            "think": self.think,
        }
        if self.response_format is not None:
            payload["format"] = self.response_format

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                while True:
                    raw = resp.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(str(data["error"]))
                    message = data.get("message") or {}
                    content = message.get("content") or ""
                    if content:
                        yield content
                    if data.get("done"):
                        break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama chat HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama chat failed: {exc}") from exc
