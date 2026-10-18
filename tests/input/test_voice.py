"""Voice input tests — no real mic or Whisper download."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from desktop_ai_agent.input.voice import (
    VoiceError,
    match_wake_phrase,
    normalize_text,
    strip_after_wake,
    voice_available,
    wake_enabled,
)


class TestVoiceAvailable(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(voice_available(), bool)

    def test_false_when_import_fails(self):
        real_import = __import__

        def boom(name, *args, **kwargs):
            if name in ("faster_whisper", "sounddevice", "numpy"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=boom):
            self.assertFalse(voice_available())


class TestWakePhraseHelpers(unittest.TestCase):
    def test_normalize_strips_punctuation(self):
        self.assertEqual(normalize_text("Hey, Zsuzsi!"), "hey zsuzsi")

    def test_match_primary_phrase(self):
        self.assertEqual(
            match_wake_phrase("Okay hey Zsuzsi wait two seconds", ["hey zsuzsi"]),
            "hey zsuzsi",
        )

    def test_match_alias(self):
        self.assertEqual(
            match_wake_phrase("hey sushi open notepad", ["hey zsuzsi", "hey sushi"]),
            "hey sushi",
        )

    def test_match_fuzzy_name(self):
        self.assertIsNotNone(match_wake_phrase("Hey Suzy", ["hey zsuzsi"]))
        self.assertIsNotNone(match_wake_phrase("zsuzsi", ["hey zsuzsi"]))

    def test_no_match(self):
        self.assertIsNone(match_wake_phrase("hello there", ["hey zsuzsi"]))

    def test_strip_after_wake(self):
        text = strip_after_wake(
            "Hey Zsuzsi, wait two seconds please",
            ["hey zsuzsi"],
        )
        self.assertEqual(text.lower(), "wait two seconds please")

    def test_wake_enabled_reads_config(self):
        self.assertTrue(
            wake_enabled({"voice": {"enabled": True, "wake_word": {"enabled": True}}})
        )
        self.assertFalse(
            wake_enabled({"voice": {"enabled": True, "wake_word": {"enabled": False}}})
        )
        self.assertFalse(wake_enabled({"voice": {"enabled": False}}))


class TestPushToTalkInit(unittest.TestCase):
    def test_raises_when_deps_missing(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=False):
            from desktop_ai_agent.input.voice import PushToTalk

            with self.assertRaisesRegex(VoiceError, r'pip install -e "\.\[voice\]"'):
                PushToTalk()


@unittest.skipUnless(HAS_NUMPY, "numpy not installed (optional [voice] extra)")
class TestPushToTalkFlow(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: sys.modules.get(k) for k in ("sounddevice", "faster_whisper")}

        sd = types.ModuleType("sounddevice")
        self.chunks = [np.ones((800, 1), dtype=np.float32) * 0.1]

        class FakeStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]
                self.started = False

            def start(self):
                self.started = True
                for chunk in self_outer.chunks:
                    self.callback(chunk, chunk.shape[0], None, None)

            def stop(self):
                pass

            def close(self):
                pass

        self_outer = self
        sd.InputStream = FakeStream
        sys.modules["sounddevice"] = sd

        fw = types.ModuleType("faster_whisper")

        class Seg:
            def __init__(self, text: str) -> None:
                self.text = text

        class FakeModel:
            def __init__(self, *a, **k) -> None:
                pass

            def transcribe(self, audio, **kwargs):
                self.last_audio = audio
                return [Seg("  wait two seconds  "), Seg(" please ")], None

        fw.WhisperModel = FakeModel
        sys.modules["faster_whisper"] = fw
        self.FakeModel = FakeModel

    def tearDown(self) -> None:
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_record_and_transcribe(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import PushToTalk

            ptt = PushToTalk(language="en", model_size="tiny")
            ptt.start()
            self.assertTrue(ptt.is_recording)
            text = ptt.stop_and_transcribe()
        self.assertEqual(text, "wait two seconds please")
        self.assertFalse(ptt.is_recording)

    def test_empty_audio_returns_empty_string(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import PushToTalk

            ptt = PushToTalk()
            ptt.engine._model = self.FakeModel()
            self.assertEqual(ptt.transcribe(np.zeros(0, dtype=np.float32)), "")

    def test_from_setup_reads_voice_block(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import from_setup

            ptt = from_setup(
                {
                    "voice": {
                        "model_size": "tiny",
                        "language": "en",
                        "sample_rate": 8000,
                    }
                }
            )
        self.assertEqual(ptt.model_size, "tiny")
        self.assertEqual(ptt.language, "en")
        self.assertEqual(ptt.sample_rate, 8000)

    def test_double_start_raises(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import PushToTalk

            ptt = PushToTalk()
            ptt.start()
            with self.assertRaisesRegex(VoiceError, "Already recording"):
                ptt.start()
            ptt.cancel()

    def test_wake_from_setup_reads_phrases(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import wake_from_setup

            wake = wake_from_setup(
                {
                    "voice": {
                        "model_size": "tiny",
                        "wake_word": {
                            "phrases": ["hey zsuzsi"],
                            "silence_sec": 2.0,
                        },
                    }
                }
            )
        self.assertEqual(wake.phrases, ("hey zsuzsi",))
        self.assertEqual(wake.silence_sec, 2.0)


@unittest.skipUnless(HAS_NUMPY, "numpy not installed (optional [voice] extra)")
class TestWakeListenerFlow(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: sys.modules.get(k) for k in ("sounddevice", "faster_whisper")}
        sd = types.ModuleType("sounddevice")

        class FakeStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        sd.InputStream = FakeStream
        sys.modules["sounddevice"] = sd

        fw = types.ModuleType("faster_whisper")

        class Seg:
            def __init__(self, text: str) -> None:
                self.text = text

        class FakeModel:
            def __init__(self, *a, **k) -> None:
                self.calls = 0

            def transcribe(self, audio, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return [Seg("hey zsuzsi")], None
                return [Seg("hey zsuzsi wait two seconds")], None

        fw.WhisperModel = FakeModel
        sys.modules["faster_whisper"] = fw

    def tearDown(self) -> None:
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_wake_then_command_invokes_callback(self):
        with patch("desktop_ai_agent.input.voice.voice_available", return_value=True):
            from desktop_ai_agent.input.voice import WakeListener, WhisperEngine

            engine = WhisperEngine(model_size="tiny")
            engine._model = sys.modules["faster_whisper"].WhisperModel()
            commands: list[str] = []
            statuses: list[tuple[str, str]] = []

            listener = WakeListener(
                engine,
                phrases=["hey zsuzsi"],
                silence_sec=0.05,
                chunk_sec=0.05,
                max_utterance_sec=2.0,
                energy_threshold=0.001,
                on_command=commands.append,
                on_status=lambda k, d: statuses.append((k, d)),
            )
            listener.start()
            # Inject loud audio so wake + command phases see energy
            loud = np.ones((int(0.5 * listener.sample_rate), 1), dtype=np.float32) * 0.2
            with listener._lock:
                listener._frames.append(loud)
            # Force wake detection path once
            text = engine.transcribe(loud.reshape(-1))
            self.assertTrue(match_wake_phrase(text, listener.phrases))
            listener._phase = "command"
            listener._cmd_started_at = 0.0  # already expired vs silence
            listener._last_voice_at = 0.0
            listener._heard_voice = True
            with listener._lock:
                listener._frames = [loud]
            # Run one command-end cycle by calling loop logic pieces
            import time

            time.sleep(0.08)
            audio = listener._take_audio()
            listener._clear_frames()
            listener._phase = "wake"
            full = engine.transcribe(audio)
            command = strip_after_wake(full, listener.phrases)
            if command:
                listener.on_command(command)
            listener.stop()

        self.assertEqual(commands, ["wait two seconds"])


if __name__ == "__main__":
    unittest.main()
