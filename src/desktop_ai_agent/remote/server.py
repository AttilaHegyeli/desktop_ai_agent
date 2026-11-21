"""Tiny authenticated HTTP server for the phone call (stdlib only).

Bind to 127.0.0.1 and expose it through a tunnel (see README). Every /api call
needs ``Authorization: Bearer <REMOTE_TOKEN>``.
"""
from __future__ import annotations

import hmac
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from desktop_ai_agent.remote.call_session import CallController, CallError
from desktop_ai_agent.remote.page import PAGE_HTML

MIN_TOKEN_LEN = 12
MAX_BODY = 1_000_000
FAIL_LIMIT = 5
GLOBAL_FAIL_LIMIT = 25
FAIL_WINDOW_S = 300.0


class RemoteConfigError(ValueError):
    pass


def validate_token(token: str | None) -> str:
    token = (token or "").strip()
    if len(token) < MIN_TOKEN_LEN:
        raise RemoteConfigError(
            f"The phone password must be at least {MIN_TOKEN_LEN} characters "
            "(it is reachable from the internet, so make it long and unique)."
        )
    return token


class _AuthGuard:
    """Failed-auth lockout, per client and globally.

    The client key may come from a forwarded header that a caller behind a plain
    tunnel could spoof to dodge the per-client limit, hence the global cap too.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(key, []) if now - t < FAIL_WINDOW_S]
        self._fails[key] = recent
        return recent

    def locked(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            return (
                len(self._recent(key, now)) >= FAIL_LIMIT
                or len(self._recent("*", now)) >= GLOBAL_FAIL_LIMIT
            )

    def fail(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._fails.setdefault(key, []).append(now)
            self._fails.setdefault("*", []).append(now)
            if len(self._fails) > 1000:  # bound memory against key rotation
                self._fails = {"*": self._recent("*", now)}

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


class RemoteServer:
    def __init__(
        self,
        *,
        token: str,
        call: CallController,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._token = validate_token(token)
        self.call = call
        self.host = host
        self.port = port
        self._guard = _AuthGuard()
        self._httpd: ThreadingHTTPServer | None = None
        self._stop_watchdog = threading.Event()

    @property
    def bound_port(self) -> int:
        assert self._httpd is not None
        return int(self._httpd.server_address[1])

    def start(self) -> None:
        server = self

        class Handler(_Handler):
            owner = server

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="remote-http"
        ).start()
        self._stop_watchdog.clear()
        threading.Thread(target=self._watchdog, daemon=True, name="remote-watchdog").start()

    def stop(self) -> None:
        self._stop_watchdog.set()
        self.call.end("shutdown")
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _watchdog(self) -> None:
        while not self._stop_watchdog.wait(1.0):
            try:
                self.call.check_timeouts()
            except Exception:  # noqa: BLE001
                pass

    def set_token(self, token: str) -> None:
        """Change the password while running (validated first)."""
        self._token = validate_token(token)

    def check_token(self, header: str) -> bool:
        scheme, _, value = (header or "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(value.strip().encode(), self._token.encode())


class _Handler(BaseHTTPRequestHandler):
    owner: RemoteServer
    timeout = 300
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass

    # ---- helpers -------------------------------------------------------
    def _client_key(self) -> str:
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1"):  # behind the tunnel: trust forwarded ip
            fwd = self.headers.get("CF-Connecting-IP") or self.headers.get(
                "X-Forwarded-For", ""
            )
            if fwd:
                return fwd.split(",")[0].strip()
        return ip

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict[str, Any]) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _authorized(self) -> bool:
        key = self._client_key()
        guard = self.owner._guard
        if guard.locked(key):
            self._json(429, {"error": "Too many failed attempts. Try again later."})
            return False
        if not self.owner.check_token(self.headers.get("Authorization", "")):
            guard.fail(key)
            self._json(401, {"error": "Unauthorized"})
            return False
        guard.ok(key)
        return True

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            # Drain a modest body so the client can read our reply; then close.
            remaining = length if 0 < length <= 8 * MAX_BODY else 0
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self.close_connection = True
            self._json(413, {"error": "Body too large"})
            return None
        return self.rfile.read(length) if length else b""

    # ---- routes --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        if path in ("/", "/index.html"):
            self._send(200, PAGE_HTML.encode(), "text/html; charset=utf-8")
            return
        if path.startswith("/api/audio/"):
            if not self._authorized():
                return
            audio_id = path[len("/api/audio/"):].removesuffix(".mp3")
            data = self.owner.call.get_audio(audio_id)
            if data is None:
                self._json(404, {"error": "Not found"})
            else:
                self._send(200, data, "audio/mpeg")
            return
        if path.startswith("/api/job/"):
            if not self._authorized():
                return
            query = parse_qs(parts.query)
            try:
                since = int(query.get("since", ["0"])[0])
                wait = float(query.get("wait", ["0"])[0])
            except ValueError:
                self._json(400, {"error": "Bad query"})
                return
            try:
                snap = self.owner.call.poll_job(path[len("/api/job/"):], since, wait)
            except CallError as exc:
                self._json(exc.code, {"error": exc.message})
                return
            audio = snap.pop("audio")
            snap["audio_url"] = f"/api/audio/{audio}.mp3" if audio else None
            self._json(200, snap)
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._json(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        body = self._read_body()
        if body is None:
            return
        call = self.owner.call
        try:
            if path == "/api/call/start":
                call.start()
                self._json(200, {"ok": True})
            elif path == "/api/call/end":
                call.end("hangup")
                self._json(200, {"ok": True})
            elif path == "/api/call/heartbeat":
                call.heartbeat()
                self._json(200, {"ok": True})
            elif path == "/api/stop":
                call.stop_task()
                self._json(200, {"ok": True})
            elif path == "/api/utterance":
                self._json(200, {"job": call.submit_utterance(body)})
            else:
                self._json(404, {"error": "Not found"})
        except CallError as exc:
            self._json(exc.code, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"Server error: {exc}"})
