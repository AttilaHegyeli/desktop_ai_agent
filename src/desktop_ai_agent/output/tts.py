"""Speech output: neural (edge-tts) or classic Windows SAPI (pyttsx3).

Optional deps: pip install -e ".[tts]"
"""
from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import threading
import time
from typing import Any, Callable

DoneFn = Callable[[], None]

MAX_SPEAK_CHARS = 500
_DEFAULT_WAKE_ACK = "yes my lord?"
_DEFAULT_EDGE_VOICE = "en-US-JennyNeural"
_DEFAULT_ENGINE = "edge"


class TtsError(RuntimeError):
    """TTS failure with a user-facing message."""


def edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        import pygame  # noqa: F401
    except ImportError:
        return False
    return True


def sapi_available() -> bool:
    try:
        import pyttsx3  # noqa: F401
    except ImportError:
        return False
    return True


def tts_available() -> bool:
    """True when at least one TTS backend can be imported."""
    return edge_available() or sapi_available()


def _require_tts(engine: str) -> None:
    if engine == "edge" and not edge_available():
        raise TtsError(
            'Neural TTS deps missing. Install with: pip install -e ".[tts]"'
        )
    if engine == "sapi" and not sapi_available():
        raise TtsError(
            'SAPI TTS deps missing. Install with: pip install -e ".[tts]"'
        )
    if engine not in ("edge", "sapi"):
        raise TtsError(f"Unknown TTS engine: {engine!r} (use 'edge' or 'sapi')")


def tts_enabled(setup: dict[str, Any] | None) -> bool:
    """True when setup enables TTS (default True when section missing)."""
    if not setup:
        return True
    out = setup.get("output") or {}
    tts = out.get("tts") or {}
    return bool(tts.get("enabled", True))


def from_setup(setup: dict[str, Any] | None = None) -> Speaker:
    """Build a Speaker from setup.json ``output.tts`` (or defaults)."""
    cfg = ((setup or {}).get("output") or {}).get("tts") or {}
    engine = str(cfg.get("engine") or _DEFAULT_ENGINE).lower().strip()
    if engine == "edge" and not edge_available() and sapi_available():
        engine = "sapi"
    voice = cfg.get("voice")
    if voice is None and engine == "edge":
        voice = _DEFAULT_EDGE_VOICE
    return Speaker(
        engine=engine,
        rate=cfg.get("rate", 180 if engine == "sapi" else "+0%"),
        volume=float(cfg.get("volume", 1.0)),
        voice=voice,
        wake_ack=str(cfg.get("wake_ack") or _DEFAULT_WAKE_ACK),
    )


def _edge_rate(rate: Any) -> str:
    """Normalize rate for edge-tts (e.g. '+0%', '-10%')."""
    if isinstance(rate, str) and rate.strip():
        return rate.strip()
    try:
        n = int(rate)
    except (TypeError, ValueError):
        return "+0%"
    # Map ~SAPI wpm around 180 → percent delta for edge.
    pct = int(max(-50, min(50, round((n - 180) / 180 * 50))))
    return f"{pct:+d}%"


class Speaker:
    """Queue text and speak it on a background thread (non-blocking for Tk)."""

    def __init__(
        self,
        *,
        engine: str = _DEFAULT_ENGINE,
        rate: Any = 180,
        volume: float = 1.0,
        voice: str | None = None,
        wake_ack: str = _DEFAULT_WAKE_ACK,
    ) -> None:
        self.engine = engine if engine in ("edge", "sapi") else _DEFAULT_ENGINE
        self.rate = rate
        self.volume = max(0.0, min(1.0, float(volume)))
        self.voice = voice
        self.wake_ack = wake_ack
        self._q: queue.Queue[tuple[str, DoneFn | None] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._speaking = False
        self._stop_playback = threading.Event()

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def speak(self, text: str, *, on_done: DoneFn | None = None) -> None:
        """Enqueue text to speak. Empty/whitespace is a no-op (still calls on_done)."""
        cleaned = (text or "").strip()
        if not cleaned:
            if on_done is not None:
                on_done()
            return
        if len(cleaned) > MAX_SPEAK_CHARS:
            cleaned = cleaned[:MAX_SPEAK_CHARS] + "..."
        self._ensure_worker()
        self._q.put((cleaned, on_done))

    def speak_wake_ack(self, *, on_done: DoneFn | None = None) -> None:
        """Speak the configured wake acknowledgement phrase."""
        self.speak(self.wake_ack, on_done=on_done)

    def stop(self) -> None:
        """Stop the worker after draining; safe to call on app exit."""
        if self._worker is None:
            return
        self._stop_playback.set()
        self._q.put(None)
        self._worker.join(timeout=8.0)
        self._worker = None
        self._stop_playback.clear()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_playback.clear()
            self._worker = threading.Thread(
                target=self._run, daemon=True, name="tts-speaker"
            )
            self._worker.start()

    def _run(self) -> None:
        _require_tts(self.engine)

        while True:
            item = self._q.get()
            if item is None:
                break
            text, on_done = item
            self._speaking = True
            try:
                if self.engine == "edge":
                    self._speak_edge(text)
                else:
                    self._speak_sapi(text)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._speaking = False
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:  # noqa: BLE001
                        pass

    def _speak_edge(self, text: str) -> None:
        """Neural Microsoft voices via edge-tts + pygame playback."""
        import edge_tts
        import pygame

        voice = self.voice or _DEFAULT_EDGE_VOICE
        rate = _edge_rate(self.rate)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        path = tmp.name
        tmp.close()
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)

            async def _save() -> None:
                await communicate.save(path)

            asyncio.run(_save())
            if self._stop_playback.is_set():
                return

            # Dedicated mixer init; quit after each clip so Tk/threading stay stable.
            pygame.mixer.init()
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if self._stop_playback.is_set():
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.05)
            finally:
                try:
                    pygame.mixer.music.unload()
                except Exception:  # noqa: BLE001
                    pass
                pygame.mixer.quit()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _speak_sapi(self, text: str) -> None:
        """Classic Windows SAPI. Fresh engine per utterance (reuse goes silent)."""
        import pyttsx3

        engine = pyttsx3.init()
        try:
            try:
                engine.setProperty("rate", int(self.rate))
            except (TypeError, ValueError):
                engine.setProperty("rate", 180)
            engine.setProperty("volume", self.volume)
            if self.voice:
                self._select_sapi_voice(engine, self.voice)
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _select_sapi_voice(engine: Any, needle: str) -> None:
        """Pick first SAPI voice whose id or name contains ``needle``."""
        needle_l = needle.lower()
        for v in engine.getProperty("voices") or []:
            vid = str(getattr(v, "id", "") or "")
            name = str(getattr(v, "name", "") or "")
            if needle_l in vid.lower() or needle_l in name.lower():
                engine.setProperty("voice", vid)
                return
