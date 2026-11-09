"""OpenRouter chat client (OpenAI-compatible) matching the ChatClient surface."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from desktop_ai_agent.core.chat_client import ChatCancelled, ChatTurn

UsageFn = Callable[[int], None]


class OpenRouterError(RuntimeError):
    """Transport / API failure that the orchestrator may escalate away from."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retriable: bool = True,
        skip_api_fallback: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retriable = retriable
        # Billing / key limit: still recover on local Gemma, but do not try another paid API model.
        self.skip_api_fallback = skip_api_fallback


class OpenRouterClient:
    """Core AI via OpenRouter /chat/completions. Same methods as ChatClient for run_task."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        system: str | None = None,
        temperature: float = 0.2,
        context_tokens: int = 128_000,
        app_title: str = "Desktop AI Agent",
        http_referer: str = "https://github.com/desktop-ai-agent",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.system = system or ""
        self.temperature = temperature
        self._context_limit = int(context_tokens)
        self.app_title = app_title
        self.http_referer = http_referer
        self.messages: list[dict[str, Any]] = []
        self.context_used = 0
        self.on_usage: UsageFn | None = None
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
        self._aborted = True

    @property
    def context_limit(self) -> int | None:
        return self._context_limit

    def _set_usage(self, used: int) -> None:
        self.context_used = used
        if self.on_usage:
            self.on_usage(used)

    def drop_images(self) -> None:
        for msg in self.messages:
            msg.pop("images", None)
            content = msg.get("content")
            if isinstance(content, list):
                # Keep text parts only if we ever stored OpenAI-shaped content locally.
                texts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                msg["content"] = "\n".join(t for t in texts if t) or ""

    def compact_tool_results(self, tool_name: str, stub: str, keep_last: int = 1) -> int:
        matches = [
            m
            for m in self.messages
            if m.get("role") == "tool" and m.get("tool_name") == tool_name
        ]
        old = matches[: max(0, len(matches) - keep_last)]
        replaced = 0
        for msg in old:
            if msg.get("content") != stub:
                msg["content"] = stub
                replaced += 1
        return replaced

    def add_user(self, text: str, images: list[str] | None = None) -> None:
        msg: dict[str, Any] = {"role": "user", "content": text}
        if images:
            msg["images"] = list(images)
        self.messages.append(msg)

    def add_tool_result(self, tool_name: str, content: str) -> None:
        call_id = self._claim_tool_call_id(tool_name)
        self.messages.append(
            {
                "role": "tool",
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "content": content,
            }
        )

    def add_auto_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        normalized = [self._normalize_tool_call(c) for c in calls]
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant":
                msg["tool_calls"] = list(msg.get("tool_calls") or []) + normalized
                return

    def recover_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        normalized = [self._normalize_tool_call(c) for c in calls]
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant":
                msg["content"] = ""
                msg["tool_calls"] = normalized
                return

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        if self._aborted:
            self._aborted = False
            raise ChatCancelled("stopped")
        payload = {
            "model": self.model,
            "messages": self._to_openai_messages(),
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        data = self._post(payload)
        if self._aborted:
            self._aborted = False
            raise ChatCancelled("stopped")

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        raw_calls = message.get("tool_calls") or []
        tool_calls = [self._normalize_tool_call(c) for c in raw_calls]

        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens") or 0
        if isinstance(prompt, int):
            self._set_usage(prompt + int(completion))

        if content and on_chunk:
            on_chunk(content)

        entry: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        self.messages.append(entry)
        return ChatTurn(content=content, tool_calls=list(tool_calls))

    # ---- internals -----------------------------------------------------------
    def _claim_tool_call_id(self, tool_name: str) -> str:
        answered = {
            m.get("tool_call_id")
            for m in self.messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        for msg in reversed(self.messages):
            if msg.get("role") != "assistant":
                continue
            for call in msg.get("tool_calls") or []:
                cid = call.get("id")
                name = (call.get("function") or {}).get("name")
                if cid and cid not in answered and name == tool_name:
                    return str(cid)
            break
        return f"call_{uuid.uuid4().hex[:12]}"

    def _normalize_tool_call(self, call: Any) -> dict[str, Any]:
        if not isinstance(call, dict):
            call = {
                "id": getattr(call, "id", None),
                "type": "function",
                "function": getattr(call, "function", None),
            }
        fn = call.get("function") or {}
        if not isinstance(fn, dict):
            fn = {"name": getattr(fn, "name", None), "arguments": getattr(fn, "arguments", {})}
        args = fn.get("arguments", {})
        if isinstance(args, dict):
            args = json.dumps(args)
        elif args is None:
            args = "{}"
        else:
            args = str(args)
        return {
            "id": str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
            "type": "function",
            "function": {"name": fn.get("name"), "arguments": args},
        }

    def _to_openai_messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in self.messages:
            role = msg.get("role")
            if role == "system":
                out.append({"role": "system", "content": msg.get("content") or ""})
            elif role == "user":
                text = msg.get("content") or ""
                images = msg.get("images") or []
                if images:
                    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
                    for b64 in images:
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            }
                        )
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": text})
            elif role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                }
                if msg.get("tool_calls"):
                    entry["tool_calls"] = [
                        self._normalize_tool_call(c) for c in msg["tool_calls"]
                    ]
                out.append(entry)
            elif role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id")
                        or f"call_{uuid.uuid4().hex[:12]}",
                        "content": msg.get("content") or "",
                    }
                )
        return out

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": self.http_referer,
                "X-Title": self.app_title,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            billing = exc.code in {401, 402, 403} or _looks_like_billing(detail)
            retriable = billing or exc.code in {429, 500, 502, 503, 504}
            raise OpenRouterError(
                f"OpenRouter HTTP {exc.code}: {detail}",
                status=exc.code,
                retriable=retriable,
                skip_api_fallback=billing,
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if self._aborted:
                raise ChatCancelled("stopped") from exc
            raise OpenRouterError(f"OpenRouter request failed: {exc}", retriable=True) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenRouterError(f"OpenRouter returned invalid JSON: {raw[:200]}", retriable=False) from exc
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else None
            blob = f"{msg} {err}"
            billing = code in {401, 402, 403} or _looks_like_billing(blob)
            raise OpenRouterError(
                f"OpenRouter error: {msg}",
                status=int(code) if isinstance(code, int) else None,
                retriable=True,
                skip_api_fallback=billing,
            )
        return data


def _looks_like_billing(text: str) -> bool:
    low = (text or "").lower()
    return any(
        token in low
        for token in (
            "key_limit",
            "limit_usd",
            "credit",
            "payment",
            "billing",
            "insufficient",
            "usage limit",
        )
    )
