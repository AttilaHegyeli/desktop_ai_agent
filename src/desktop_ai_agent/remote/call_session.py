"""Phone call state machine: PCM in -> STT -> agent -> TTS mp3 out.

Transport-agnostic: everything OS/agent specific is injected so it can be tested.

Utterances run as background *jobs* that the phone polls. A single long HTTP
request would be cut by tunnels (Cloudflare closes requests after ~100 s).
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

SAMPLE_RATE = 16000
MAX_UTTERANCE_SEC = 30
MAX_POLL_WAIT_S = 25.0
HANGUP_DELAY_S = 6.0
HANGUP_PHRASES = frozenset(
    {"hang up", "end call", "end the call", "goodbye", "good bye", "bye bye"}
)

# (text) -> (status, reply) or None when the agent is busy / not ready
SubmitFn = Callable[[str], "tuple[str, str] | None"]


class CallError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z ]", "", (text or "").lower()).strip()


class _Job:
    def __init__(self, call_id: int) -> None:
        self.call_id = call_id
        self.cond = threading.Condition()
        self.version = 0
        self.done = False
        self.state = "transcribing"  # transcribing | working | done
        self.transcript = ""
        self.reply = ""
        self.status = ""
        self.audio: str | None = None
        self.error = ""

    def update(self, **fields: Any) -> None:
        with self.cond:
            for key, value in fields.items():
                setattr(self, key, value)
            self.version += 1
            self.cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "done": self.done,
            "state": self.state,
            "transcript": self.transcript,
            "reply": self.reply,
            "status": self.status,
            "error": self.error,
            "audio": self.audio,
        }


class CallController:
    def __init__(
        self,
        *,
        transcribe: Callable[[Any], str],
        submit: SubmitFn,
        synthesize: Callable[[str], bytes],
        stop_task: Callable[[], None] = lambda: None,
        on_start: Callable[[], None] = lambda: None,
        on_end: Callable[[str], None] = lambda reason: None,
        idle_timeout_s: float = 60.0,
        max_call_s: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transcribe = transcribe
        self._submit = submit
        self._synthesize = synthesize
        self._stop_task = stop_task
        self._on_start = on_start
        self._on_end = on_end
        self._idle_timeout_s = idle_timeout_s
        self._max_call_s = max_call_s
        self._clock = clock
        self._lock = threading.Lock()
        self._active = False
        self._call_id = 0
        self._started = 0.0
        self._last_seen = 0.0
        self._working = False
        self._audio: OrderedDict[str, bytes] = OrderedDict()
        self._jobs: OrderedDict[str, _Job] = OrderedDict()

    @property
    def active(self) -> bool:
        return self._active

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._active:
                raise CallError(409, "A call is already in progress")
            self._active = True
            self._call_id += 1
            self._started = self._last_seen = self._clock()
        try:
            self._on_start()
        except Exception:
            self.end("error")
            raise

    def end(self, reason: str = "hangup", *, call_id: int | None = None) -> bool:
        """End the call. With ``call_id`` only that call ends (stale timers)."""
        with self._lock:
            if not self._active or (call_id is not None and call_id != self._call_id):
                return False
            self._active = False
            self._audio.clear()
            self._jobs.clear()
        try:
            self._stop_task()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._on_end(reason)
        except Exception:  # noqa: BLE001
            pass
        return True

    def heartbeat(self) -> None:
        self._require_active()
        self._last_seen = self._clock()

    def check_timeouts(self) -> None:
        """Called periodically by the server watchdog."""
        if not self._active:
            return
        now = self._clock()
        if now - self._started > self._max_call_s:
            self.end("max_duration")
        elif not self._working and now - self._last_seen > self._idle_timeout_s:
            self.end("timeout")

    def stop_task(self) -> None:
        self._require_active()
        self._last_seen = self._clock()
        self._stop_task()

    # ---- utterance jobs ------------------------------------------------
    def submit_utterance(self, pcm: bytes) -> str:
        """Validate and start a job; returns its id immediately."""
        self._require_active()
        if len(pcm) < 2 or len(pcm) % 2:
            raise CallError(400, "Audio must be 16-bit mono PCM")
        if len(pcm) > SAMPLE_RATE * 2 * MAX_UTTERANCE_SEC:
            raise CallError(413, "Utterance too long")
        with self._lock:
            if self._working:
                raise CallError(409, "Still working on the previous request")
            self._working = True
            call_id = self._call_id
            job_id = secrets.token_urlsafe(9)
            job = _Job(call_id)
            self._jobs[job_id] = job
            while len(self._jobs) > 5:
                self._jobs.popitem(last=False)
        self._last_seen = self._clock()
        threading.Thread(
            target=self._run_job, args=(job, pcm), daemon=True, name="call-job"
        ).start()
        return job_id

    def poll_job(self, job_id: str, since: int = 0, wait: float = 0.0) -> dict[str, Any]:
        """Snapshot of a job; waits until it changes past ``since`` (or ``wait`` s)."""
        self._require_active()
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise CallError(404, "Unknown job")
        self._last_seen = self._clock()
        wait = max(0.0, min(float(wait), MAX_POLL_WAIT_S))
        deadline = time.monotonic() + wait
        with job.cond:
            while job.version <= since and not job.done:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                job.cond.wait(left)
            snap = job.snapshot()
        self._last_seen = self._clock()
        return snap

    def get_audio(self, audio_id: str) -> bytes | None:
        with self._lock:
            return self._audio.get(audio_id)

    # ---- internals -----------------------------------------------------
    def _run_job(self, job: _Job, pcm: bytes) -> None:
        try:
            import numpy as np

            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            try:
                text = (self._transcribe(audio) or "").strip()
            except Exception as exc:  # noqa: BLE001
                job.update(
                    status="error", error=f"Could not understand audio: {exc}",
                    state="done", done=True,
                )
                return
            if not text:
                job.update(status="empty", state="done", done=True)
                return
            job.update(transcript=text, state="working")
            if _normalize(text) in HANGUP_PHRASES:
                job.update(
                    reply="Goodbye.", status="hangup",
                    audio=self._store_audio("Goodbye.", job.call_id),
                    state="done", done=True,
                )
                timer = threading.Timer(
                    HANGUP_DELAY_S, self.end, args=("voice_hangup",),
                    kwargs={"call_id": job.call_id},
                )
                timer.daemon = True
                timer.start()
                return
            result = self._submit(text)
            if result is None:
                job.update(
                    status="busy", error="The agent is busy", state="done", done=True
                )
                return
            status, reply = result
            reply = reply or ("Done." if status == "done" else f"Task {status}.")
            job.update(
                reply=reply, status=status,
                audio=self._store_audio(reply, job.call_id),
                state="done", done=True,
            )
        except Exception as exc:  # noqa: BLE001 - a job must always finish
            job.update(status="error", error=str(exc), state="done", done=True)
        finally:
            self._last_seen = self._clock()
            self._working = False

    def _require_active(self) -> None:
        if not self._active:
            raise CallError(409, "No active call")

    def _store_audio(self, reply: str, call_id: int) -> str | None:
        try:
            data = self._synthesize(reply)
        except Exception:  # noqa: BLE001 - phone still shows the text
            return None
        if not data:
            return None
        audio_id = secrets.token_urlsafe(12)
        with self._lock:
            if not self._active or call_id != self._call_id:
                return None
            self._audio[audio_id] = data
            while len(self._audio) > 5:
                self._audio.popitem(last=False)
        return audio_id
