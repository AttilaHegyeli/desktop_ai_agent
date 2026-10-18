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

from desktop_ai_agent.input.voice import VoiceError, voice_available


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
            ptt._model = self.FakeModel()
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


if __name__ == "__main__":
    unittest.main()
