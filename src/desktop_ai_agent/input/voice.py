"""Push-to-talk speech-to-text via sounddevice + faster-whisper.

Optional deps: pip install -e ".[voice]"
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np


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


class PushToTalk:
    """Record from the default mic until stop, then transcribe to text."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        model_size: str = "base",
        language: str | None = "en",
        device: str = "gpu",
        compute_type: str = "int8",
    ) -> None:
        _require_voice()
        self.sample_rate = int(sample_rate)
        self.model_size = model_size
        self.language = language or None
        self.device = device
        self.compute_type = compute_type

        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[Any] = []
        self._stream: Any = None
        self._model: Any = None

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

            def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
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

    def _get_model(self) -> Any:
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

    def transcribe(self, audio: Any) -> str:
        """Run faster-whisper on mono float32 audio at self.sample_rate."""
        if getattr(audio, "size", 0) == 0:
            return ""
        model = self._get_model()
        try:
            segments, _info = model.transcribe(
                audio,
                language=self.language,
                beam_size=1,
                vad_filter=True,
            )
            parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
        except Exception as exc:  # noqa: BLE001
            raise VoiceError(f"Transcription failed: {exc}") from exc
        return " ".join(parts).strip()

    def stop_and_transcribe(self) -> str:
        """Stop recording and return the transcript (may be empty)."""
        return self.transcribe(self.stop())

    def cancel(self) -> None:
        """Stop recording and discard audio (no transcription)."""
        try:
            self.stop()
        except VoiceError:
            pass


def from_setup(setup: dict[str, Any] | None) -> PushToTalk:
    """Build a PushToTalk from config/setup.json's ``voice`` block."""
    voice = (setup or {}).get("voice") or {}
    return PushToTalk(
        sample_rate=int(voice.get("sample_rate", 16000)),
        model_size=str(voice.get("model_size", "base")),
        language=voice.get("language", "en"),
        device=str(voice.get("device", "cpu")),
        compute_type=str(voice.get("compute_type", "int8")),
    )
