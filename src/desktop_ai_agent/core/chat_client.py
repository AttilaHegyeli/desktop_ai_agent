from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from desktop_ai_agent.core.response_format import (
    AGENT_JSON_SCHEMA,
    agent_system_prompt,
    normalize_agent_reply,
)


LogFn = Callable[[str], None]
UsageFn = Callable[[int], None]


class ChatCancelled(RuntimeError):
    """Raised by chat() when abort() was called while the model was replying."""


@dataclass
class ChatTurn:
    """One model reply: text, optional reasoning, and any tool calls it made."""

    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


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
        options: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.think = think
        self.response_format: str | dict[str, Any] | None = (
            AGENT_JSON_SCHEMA if response_format is None else response_format
        )
        self.system = system if system is not None else agent_system_prompt()
        self.normalize = normalize
        # e.g. {"temperature": 0.2, "num_ctx": 8192}. None keeps Ollama's defaults.
        self.options = options
        self.messages: list[dict[str, Any]] = []
        # Tokens the conversation fills in the model's context window.
        # Exact after each reply (Ollama's counts), estimated while streaming.
        self.context_used = 0
        self.on_usage: UsageFn | None = None  # called from the streaming thread
        self._resp: Any = None
        self._aborted = False
        self._seed()

    def _seed(self) -> None:
        if self.system:
            self.messages.append({"role": "system", "content": self.system})

    def clear(self) -> None:
        self.messages.clear()
        self._seed()
        self._set_usage(0)

    def abort(self) -> None:
        """Cut the reply that is streaming right now (safe from any thread).

        Closing the connection also makes Ollama stop generating.
        """
        self._aborted = True
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - already closed / mid-read
                pass

    @property
    def context_limit(self) -> int | None:
        return (self.options or {}).get("num_ctx")

    def _set_usage(self, used: int) -> None:
        self.context_used = used
        if self.on_usage:
            self.on_usage(used)

    def drop_images(self) -> None:
        """Remove attached images from history so a fresh screenshot can replace them."""
        for msg in self.messages:
            msg.pop("images", None)

    # ---- plain text chat (the original JSON scriptwriter path) --------------
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

    # ---- tool-calling chat (used by the orchestrator) -----------------------
    def add_user(self, text: str, images: list[str] | None = None) -> None:
        """Add a user message. `images` are base64 strings (e.g. a screenshot)."""
        msg: dict[str, Any] = {"role": "user", "content": text}
        if images:
            msg["images"] = images
        self.messages.append(msg)

    def add_tool_result(self, tool_name: str, content: str) -> None:
        """Feed an executor result (or rejection reason) back to the model."""
        self.messages.append({"role": "tool", "tool_name": tool_name, "content": content})

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        """Run one model turn on the current history. Does NOT add a user message.

        When `tools` is given, the JSON `format` constraint is dropped (it is
        meant for plain JSON replies and can interfere with tool calling), and the
        reply is NOT passed through normalize_agent_reply.
        """
        turn = ChatTurn()
        parts: list[str] = []
        thinking: list[str] = []
        for msg in self._iter_messages(tools):
            piece = msg.get("content") or ""
            if piece:
                parts.append(piece)
                if on_chunk:
                    on_chunk(piece)
            thought = msg.get("thinking") or ""
            if thought:
                thinking.append(thought)
            turn.tool_calls.extend(msg.get("tool_calls") or [])
        turn.content = "".join(parts)
        turn.thinking = "".join(thinking)

        entry: dict[str, Any] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            entry["tool_calls"] = turn.tool_calls
        self.messages.append(entry)
        return turn

    # ---- helpers and HTTP plumbing -------------------------------------------
    def _finalize(self, raw: str, user_text: str = "") -> str:
        if not self.normalize:
            return raw
        return normalize_agent_reply(raw, user_text=user_text)

    def _payload(self, tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "stream": True,
            "think": self.think,
        }
        if self.options:
            payload["options"] = self.options
        if tools:
            payload["tools"] = tools
        elif self.response_format is not None:
            payload["format"] = self.response_format
        return payload

    def _stream(self) -> Iterator[str]:
        for message in self._iter_messages(None):
            content = message.get("content") or ""
            if content:
                yield content

    def _iter_messages(self, tools: list[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
        body = json.dumps(self._payload(tools)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self._aborted = False
        start = self.context_used
        streamed = 0
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                self._resp = resp
                if self._aborted:  # abort() landed before the response existed
                    raise ChatCancelled("stopped")
                while True:
                    raw = resp.readline()
                    if not raw or self._aborted:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(str(data["error"]))
                    message = data.get("message") or {}
                    if message:
                        streamed += 1  # ~1 token per streamed chunk: live estimate only
                        self._set_usage(start + streamed)
                        yield message
                    if data.get("done"):
                        self._record_usage(start, data)
                        break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama chat HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if self._aborted:
                raise ChatCancelled("stopped") from exc
            if isinstance(exc, urllib.error.URLError):
                raise RuntimeError(f"Ollama chat failed: {exc}") from exc
            raise
        finally:
            self._resp = None
        if self._aborted:
            raise ChatCancelled("stopped")

    def _record_usage(self, start: int, data: dict[str, Any]) -> None:
        prompt = data.get("prompt_eval_count")
        reply = data.get("eval_count") or 0
        if not isinstance(prompt, int):
            return  # keep the streamed estimate
        # When Ollama reuses its prompt cache, prompt_eval_count only covers the
        # NEW tokens, so a count below what we already hold is a delta, not a total.
        used = prompt + reply if prompt >= start else start + prompt + reply
        self._set_usage(used)