"""Output layer: feedback to the user (TTS, later HUD)."""

from desktop_ai_agent.output.tts import (
    Speaker,
    TtsError,
    from_setup,
    text_for_speech,
    tts_available,
    tts_enabled,
)

__all__ = [
    "Speaker",
    "TtsError",
    "from_setup",
    "text_for_speech",
    "tts_available",
    "tts_enabled",
]
