"""Speech input: push-to-talk and wake-word listening via sounddevice + faster-whisper.

Optional deps: pip install -e ".[voice]"
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

StatusFn = Callable[[str, str], None]
CommandFn = Callable[[str], None]

_DEFAULT_WAKE_PHRASES = (
    "hey zsuzsi",
    "hey susie",
    "hey sushi",
    "hey sooshi",
    "hey suzy",
    "hey suzie",
    "hey soosie",
    "hey zuzi",
)

# Name tokens Whisper often produces for "Zsuzsi" (ZHOO-shee).
_WAKE_NAME_ALIASES = frozenset(
    {
        "zsuzsi",
        "susie",
        "sushi",
        "sooshi",
        "suzy",
        "suzie",
        "soosie",
        "susy",
        "zuzi",
        "juzi",
        "juicy",
        "suzi",
        "zoosie",
        "zhuzhi",
    }
)
_WAKE_GREETINGS = frozenset({"hey", "hi", "ok", "okay", "yo", "a"})


class VoiceError(RuntimeError):
    """Mic / STT failure with a user-facing message."""


def voice_available() -> bool:
    """True when the optional voice dependencies can be imported."""
    try:
        import faster_whisper  # noqa: F401
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
    except ImportError:
        return False
    return True


def _require_voice() -> None:
    if not voice_available():
        raise VoiceError(
            'Voice deps missing. Install with: pip install -e ".[voice]"'
        )


def normalize_text(text: str) -> str:
    """Lowercase and strip punctuation for wake-phrase matching."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _name_close(token: str, target: str = "zsuzsi") -> bool:
    """Loose match for STT mangling of Zsuzsi (length + shared letters)."""
    if token in _WAKE_NAME_ALIASES:
        return True
    if abs(len(token) - len(target)) > 2 or len(token) < 3:
        return False
    # Share enough characters with "zsuzsi" / "susie"
    shared = sum(1 for ch in set(token) if ch in set(target + "susie"))
    return shared >= 3 and ("s" in token or "z" in token or "j" in token)


def match_wake_phrase(text: str, phrases: list[str] | tuple[str, ...]) -> str | None:
    """Return a matched wake phrase, or None.

    Accepts configured phrases plus fuzzy ``hey <name>`` / bare name aliases
    (Whisper often mangles “Zsuzsi”).
    """
    hay = normalize_text(text)
    if not hay:
        return None
    for phrase in phrases:
        needle = normalize_text(phrase)
        if needle and needle in hay:
            return phrase

    tokens = hay.split()
    for i, tok in enumerate(tokens):
        if tok in _WAKE_GREETINGS and i + 1 < len(tokens) and _name_close(tokens[i + 1]):
            return f"{tok} {tokens[i + 1]}"
        if _name_close(tok) and (i == 0 or tokens[i - 1] in _WAKE_GREETINGS):
            return tok if i == 0 else f"{tokens[i - 1]} {tok}"
    # Lone name if the whole short utterance is basically just the name
    if len(tokens) <= 3 and any(_name_close(t) for t in tokens):
        for t in tokens:
            if _name_close(t):
                return t
    return None


def strip_after_wake(text: str, phrases: list[str] | tuple[str, ...]) -> str:
    """Remove the wake phrase (and anything before it) from the transcript."""
    matched = match_wake_phrase(text, phrases)
    hay = normalize_text(text)
    if not matched:
        return text.strip()
    needle = normalize_text(matched)
    idx = hay.find(needle)
    if idx < 0:
        # Fuzzy match may not be a contiguous substring of hay equal to needle
        # Drop up through the name token.
        tokens = hay.split()
        cut = 0
        for i, tok in enumerate(tokens):
            if _name_close(tok):
                cut = i + 1
                break
            if tok in _WAKE_GREETINGS:
                continue
        rest_tokens = tokens[cut:]
        return " ".join(rest_tokens).strip()
    best_end = idx + len(needle)
    for i in range(len(text) + 1):
        if len(normalize_text(text[:i])) >= best_end:
            return text[i:].strip(" .,!?:;-")
    return ""


def _rms(audio: Any) -> float:
    import numpy as np

    if getattr(audio, "size", 0) == 0:
        return 0.0
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    return float(np.sqrt(np.mean(np.square(x))))


class WhisperEngine:
    """Cached faster-whisper model shared by PTT and wake listening."""

    def __init__(
        self,
        *,
        model_size: str = "base",
        language: str | None = "en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        _require_voice()
        self.model_size = model_size
        self.language = language or None
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                try:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise VoiceError(
                        f"Could not load Whisper model '{self.model_size}': {exc}"
                    ) from exc
            return self._model

    def transcribe(
        self,
        audio: Any,
        *,
        language: str | None = None,
        vad_filter: bool = True,
        use_engine_language: bool = True,
    ) -> str:
        """Transcribe mono float32 audio.

        Prefer ``use_engine_language=True`` (config language, English by default).
        """
        if getattr(audio, "size", 0) == 0:
            return ""
        model = self._get_model()
        lang = self.language if use_engine_language else language
        try:
            with self._lock:
                segments, _info = model.transcribe(
                    audio,
                    language=lang,
                    beam_size=1,
                    vad_filter=vad_filter,
                )
                parts = [
                    seg.text.strip()
                    for seg in segments
                    if seg.text and seg.text.strip()
                ]
        except Exception as exc:  # noqa: BLE001
            raise VoiceError(f"Transcription failed: {exc}") from exc
        return " ".join(parts).strip()


class PushToTalk:
    """Record from the default mic until stop, then transcribe to text."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        model_size: str = "base",
        language: str | None = "en",
        device: str = "cpu",
        compute_type: str = "int8",
        engine: WhisperEngine | None = None,
    ) -> None:
        _require_voice()
        self.sample_rate = int(sample_rate)
        self.engine = engine or WhisperEngine(
            model_size=model_size,
            language=language,
            device=device,
            compute_type=compute_type,
        )
        # Compat attributes used by tests / from_setup checks
        self.model_size = self.engine.model_size
        self.language = self.engine.language
        self.device = self.engine.device
        self.compute_type = self.engine.compute_type

        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[Any] = []
        self._stream: Any = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Begin capturing microphone audio on a background callback."""
        import sounddevice as sd

        with self._lock:
            if self._recording:
                raise VoiceError("Already recording")
            self._frames = []
            self._recording = True

            def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                if self._recording:
                    self._frames.append(indata.copy())

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=callback,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self._recording = False
                self._stream = None
                raise VoiceError(f"Could not open microphone: {exc}") from exc

    def stop(self) -> Any:
        """Stop capture and return mono float32 audio (may be empty)."""
        import numpy as np

        with self._lock:
            if not self._recording and self._stream is None:
                raise VoiceError("Not recording")
            self._recording = False
            stream = self._stream
            self._stream = None
            frames = self._frames
            self._frames = []

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames, axis=0).reshape(-1)
        return audio.astype(np.float32, copy=False)

    def transcribe(self, audio: Any) -> str:
        return self.engine.transcribe(audio)

    def stop_and_transcribe(self) -> str:
        return self.transcribe(self.stop())

    def cancel(self) -> None:
        try:
            self.stop()
        except VoiceError:
            pass


class WakeListener:
    """Continuous mic listen: wake phrase → capture until silence → on_command(text)."""

    def __init__(
        self,
        engine: WhisperEngine,
        *,
        sample_rate: int = 16000,
        phrases: list[str] | tuple[str, ...] = _DEFAULT_WAKE_PHRASES,
        silence_sec: float = 3.0,
        post_wake_grace_sec: float = 5.0,
        max_utterance_sec: float = 30.0,
        chunk_sec: float = 0.8,
        energy_threshold: float = 0.005,
        on_command: CommandFn | None = None,
        on_status: StatusFn | None = None,
    ) -> None:
        _require_voice()
        self.engine = engine
        self.sample_rate = int(sample_rate)
        self.phrases = tuple(phrases) if phrases else _DEFAULT_WAKE_PHRASES
        self.silence_sec = float(silence_sec)
        self.post_wake_grace_sec = float(post_wake_grace_sec)
        self.max_utterance_sec = float(max_utterance_sec)
        self.chunk_sec = float(chunk_sec)
        self.energy_threshold = float(energy_threshold)
        self.on_command = on_command or (lambda _t: None)
        self.on_status = on_status or (lambda _k, _d: None)

        self._lock = threading.Lock()
        self._running = False
        self._paused = False
        self._stream: Any = None
        self._thread: threading.Thread | None = None
        self._frames: list[Any] = []
        self._phase = "wake"  # wake | command
        self._cmd_started_at = 0.0
        self._last_voice_at = 0.0
        self._heard_voice = False
        self._last_miss_log = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._running:
                return
            self._running = True
            self._paused = False
            self._frames = []
            self._phase = "wake"
            self._heard_voice = False

            def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                # No lock here: sounddevice may invoke this while start() still holds _lock.
                if self._running and not self._paused:
                    self._frames.append(indata.copy())

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=callback,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self._running = False
                self._stream = None
                raise VoiceError(f"Could not open microphone: {exc}") from exc

            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="wake-listener"
            )
            self._thread.start()
        self.on_status("listening", "hey Zsuzsi")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            stream = self._stream
            self._stream = None
            self._frames = []
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def pause(self, *, keep_command: bool = False) -> None:
        """Release the mic so TTS / push-to-talk can use it; keep the worker alive.

        keep_command=True: stay in command phase (wake-ack TTS). Default resets to wake.
        """
        self._paused = True
        with self._lock:
            stream = self._stream
            self._stream = None
            self._frames = []
            if keep_command and self._phase == "command":
                pass  # keep phase; timers refreshed on resume
            else:
                self._phase = "wake"
                self._heard_voice = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        self.on_status("paused", "mic")

    def resume(self, *, refresh_command: bool = False) -> None:
        """Re-open the mic after TTS / push-to-talk / agent busy.

        refresh_command=True: after wake-ack TTS, restart the command grace timer
        so the user can speak the task once the ack finishes.
        """
        import sounddevice as sd

        if not self._running:
            return
        with self._lock:
            self._frames = []
            staying_in_command = refresh_command and self._phase == "command"
            if staying_in_command:
                now = time.monotonic()
                self._cmd_started_at = now
                self._last_voice_at = now
                self._heard_voice = False
            else:
                self._phase = "wake"
                self._heard_voice = False
            if self._stream is None:
                def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                    if self._running and not self._paused:
                        self._frames.append(indata.copy())

                try:
                    self._stream = sd.InputStream(
                        samplerate=self.sample_rate,
                        channels=1,
                        dtype="float32",
                        callback=callback,
                    )
                    self._stream.start()
                except Exception as exc:  # noqa: BLE001
                    self.on_status("error", f"Could not reopen microphone: {exc}")
                    return
            self._paused = False
        if staying_in_command:
            self.on_status("capturing", "ready for command")
        else:
            self.on_status("listening", "hey Zsuzsi")

    def _take_audio(self, max_sec: float | None = None) -> Any:
        import numpy as np

        with self._lock:
            frames = self._frames
            if not frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(frames, axis=0).reshape(-1).astype(np.float32, copy=False)
            if max_sec is not None:
                max_samples = int(max_sec * self.sample_rate)
                if audio.size > max_samples:
                    audio = audio[-max_samples:]
                    self._frames = [audio.reshape(-1, 1)]
            return audio

    def _clear_frames(self) -> None:
        with self._lock:
            self._frames = []

    def _loop(self) -> None:
        import numpy as np

        wake_window = max(self.chunk_sec * 2.0, 3.0)
        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            time.sleep(self.chunk_sec)
            if not self._running or self._paused:
                continue

            try:
                if self._phase == "wake":
                    audio = self._take_audio(max_sec=wake_window)
                    if audio.size < int(0.35 * self.sample_rate):
                        continue
                    if _rms(audio) < self.energy_threshold * 0.5:
                        continue
                    # English only (engine language). VAD off so short wake
                    # clips are not wiped before matching.
                    text = self.engine.transcribe(
                        audio,
                        vad_filter=False,
                        use_engine_language=True,
                    )
                    phrase = match_wake_phrase(text, self.phrases)
                    if not phrase:
                        if text and (time.monotonic() - self._last_miss_log) >= 3.0:
                            self._last_miss_log = time.monotonic()
                            self.on_status("miss", text)
                        with self._lock:
                            max_samples = int(wake_window * self.sample_rate)
                            if self._frames:
                                buf = np.concatenate(self._frames, axis=0).reshape(-1)
                                if buf.size > max_samples:
                                    self._frames = [buf[-max_samples:].reshape(-1, 1)]
                        continue
                    self._phase = "command"
                    self._cmd_started_at = time.monotonic()
                    self._last_voice_at = time.monotonic()
                    self._heard_voice = False
                    self.on_status("heard_wake", phrase)
                    # Keep a short tail so the command start is not lost
                    with self._lock:
                        if self._frames:
                            buf = np.concatenate(self._frames, axis=0).reshape(-1)
                            tail = int(1.0 * self.sample_rate)
                            self._frames = [buf[-tail:].reshape(-1, 1)] if buf.size else []
                    continue

                # command phase
                now = time.monotonic()
                with self._lock:
                    frames = list(self._frames)
                if frames:
                    recent = np.concatenate(frames[-8:], axis=0).reshape(-1)
                    level = _rms(recent)
                    if level >= self.energy_threshold:
                        self._heard_voice = True
                        self._last_voice_at = now
                elapsed = now - self._cmd_started_at
                silent_for = now - self._last_voice_at
                # After "yes my lord?": wait grace before giving up if user
                # has not started speaking yet; then silence_sec after speech.
                end_silence = self._heard_voice and silent_for >= self.silence_sec
                end_empty = (
                    not self._heard_voice
                ) and elapsed >= self.post_wake_grace_sec
                end_max = elapsed >= self.max_utterance_sec
                if not (end_silence or end_empty or end_max):
                    continue

                if end_empty and not end_max:
                    self._clear_frames()
                    self._phase = "wake"
                    self.on_status("listening", "no command after wake")
                    continue

                audio = self._take_audio()
                self._clear_frames()
                self._phase = "wake"
                text = self.engine.transcribe(audio) if audio.size else ""
                command = strip_after_wake(text, self.phrases) if text else ""
                if command:
                    self.on_status("capturing", command)
                    self.on_command(command)
                else:
                    self.on_status("listening", "no command after wake")
            except VoiceError as exc:
                self.on_status("error", str(exc))
                self._phase = "wake"
                self._clear_frames()
            except Exception as exc:  # noqa: BLE001
                self.on_status("error", str(exc))
                self._phase = "wake"
                self._clear_frames()


def engine_from_setup(setup: dict[str, Any] | None) -> WhisperEngine:
    voice = (setup or {}).get("voice") or {}
    return WhisperEngine(
        model_size=str(voice.get("model_size", "base")),
        language=voice.get("language", "en"),
        device=str(voice.get("device", "cpu")),
        compute_type=str(voice.get("compute_type", "int8")),
    )


def from_setup(
    setup: dict[str, Any] | None,
    engine: WhisperEngine | None = None,
) -> PushToTalk:
    """Build a PushToTalk from config/setup.json's ``voice`` block."""
    voice = (setup or {}).get("voice") or {}
    eng = engine or engine_from_setup(setup)
    return PushToTalk(
        sample_rate=int(voice.get("sample_rate", 16000)),
        engine=eng,
    )


def wake_from_setup(
    setup: dict[str, Any] | None,
    *,
    on_command: CommandFn | None = None,
    on_status: StatusFn | None = None,
    engine: WhisperEngine | None = None,
) -> WakeListener:
    """Build a WakeListener from config/setup.json's ``voice.wake_word`` block."""
    voice = (setup or {}).get("voice") or {}
    wake = voice.get("wake_word") or {}
    phrases = wake.get("phrases") or list(_DEFAULT_WAKE_PHRASES)
    eng = engine or engine_from_setup(setup)
    return WakeListener(
        eng,
        sample_rate=int(voice.get("sample_rate", 16000)),
        phrases=list(phrases),
        silence_sec=float(wake.get("silence_sec", 3.0)),
        post_wake_grace_sec=float(wake.get("post_wake_grace_sec", 5.0)),
        max_utterance_sec=float(wake.get("max_utterance_sec", 30)),
        chunk_sec=float(wake.get("chunk_sec", 0.8)),
        energy_threshold=float(wake.get("energy_threshold", 0.005)),
        on_command=on_command,
        on_status=on_status,
    )


def wake_enabled(setup: dict[str, Any] | None) -> bool:
    voice = (setup or {}).get("voice") or {}
    if not voice.get("enabled", True):
        return False
    wake = voice.get("wake_word") or {}
    return bool(wake.get("enabled", True))
