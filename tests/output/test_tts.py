"""Unit tests for output TTS (mocked backends — no real audio)."""
from __future__ import annotations

import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from desktop_ai_agent.output import tts as tts_mod
from desktop_ai_agent.output.tts import (
    Speaker,
    edge_available,
    from_setup,
    tts_available,
    tts_enabled,
)


def _install_fake_pyttsx3() -> MagicMock:
    """Put a fake pyttsx3 module on sys.modules and return the engine mock."""
    engine = MagicMock()
    engine.getProperty.return_value = []
    mod = types.ModuleType("pyttsx3")
    mod.init = MagicMock(return_value=engine)  # type: ignore[attr-defined]
    sys.modules["pyttsx3"] = mod
    return engine


def _install_fake_edge() -> tuple[MagicMock, MagicMock]:
    """Fake edge_tts + pygame."""
    edge = types.ModuleType("edge_tts")
    communicate_cls = MagicMock()
    instance = MagicMock()

    async def _save(_path: str) -> None:
        return None

    instance.save = _save
    communicate_cls.return_value = instance
    edge.Communicate = communicate_cls  # type: ignore[attr-defined]
    sys.modules["edge_tts"] = edge

    pygame = types.ModuleType("pygame")
    mixer = MagicMock()
    mixer.music.get_busy.side_effect = [True, False]
    pygame.mixer = mixer  # type: ignore[attr-defined]
    sys.modules["pygame"] = pygame
    return communicate_cls, mixer


class TestTtsAvailable(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("pyttsx3", "edge_tts", "pygame"):
            sys.modules.pop(name, None)

    def test_missing_dep_returns_false(self):
        for name in ("pyttsx3", "edge_tts", "pygame"):
            sys.modules.pop(name, None)
        real_import = __import__

        def blocker(name, *args, **kwargs):
            if name in ("pyttsx3", "edge_tts", "pygame"):
                raise ImportError("nope")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocker):
            self.assertFalse(tts_available())
            self.assertFalse(edge_available())

    def test_sapi_present_returns_true(self):
        _install_fake_pyttsx3()
        self.assertTrue(tts_available())


class TestFromSetup(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("pyttsx3", "edge_tts", "pygame"):
            sys.modules.pop(name, None)

    def test_defaults_prefer_edge(self):
        _install_fake_edge()
        sp = from_setup({})
        self.assertEqual(sp.engine, "edge")
        self.assertEqual(sp.voice, "en-US-JennyNeural")
        self.assertEqual(sp.wake_ack, "yes my lord?")

    def test_reads_edge_config(self):
        _install_fake_edge()
        sp = from_setup(
            {
                "output": {
                    "tts": {
                        "engine": "edge",
                        "rate": "+10%",
                        "volume": 0.5,
                        "voice": "en-GB-SoniaNeural",
                        "wake_ack": "ready",
                    }
                }
            }
        )
        self.assertEqual(sp.engine, "edge")
        self.assertEqual(sp.rate, "+10%")
        self.assertEqual(sp.volume, 0.5)
        self.assertEqual(sp.voice, "en-GB-SoniaNeural")
        self.assertEqual(sp.wake_ack, "ready")

    def test_falls_back_to_sapi_when_edge_missing(self):
        _install_fake_pyttsx3()
        for name in ("edge_tts", "pygame"):
            sys.modules.pop(name, None)
        with patch("desktop_ai_agent.output.tts.edge_available", return_value=False):
            with patch("desktop_ai_agent.output.tts.sapi_available", return_value=True):
                sp = from_setup({"output": {"tts": {"engine": "edge"}}})
        self.assertEqual(sp.engine, "sapi")

    def test_tts_enabled_flag(self):
        self.assertTrue(tts_enabled(None))
        self.assertTrue(tts_enabled({}))
        self.assertFalse(tts_enabled({"output": {"tts": {"enabled": False}}}))


class TestSpeaker(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("pyttsx3", "edge_tts", "pygame"):
            sys.modules.pop(name, None)

    def test_empty_text_is_noop_and_calls_on_done(self):
        sp = Speaker(engine="sapi")
        done = MagicMock()
        sp.speak("   ", on_done=done)
        done.assert_called_once_with()
        self.assertIsNone(sp._worker)

    def test_speak_sapi(self):
        engine = _install_fake_pyttsx3()
        sp = Speaker(engine="sapi", rate=160, volume=0.8)
        finished = threading.Event()
        sp.speak("Hello world", on_done=finished.set)
        self.assertTrue(finished.wait(timeout=3.0))
        sp.stop()
        engine.say.assert_called_with("Hello world")
        engine.runAndWait.assert_called()

    def test_second_speak_sapi_uses_fresh_engine(self):
        _install_fake_pyttsx3()
        engines = [MagicMock(), MagicMock()]
        for e in engines:
            e.getProperty.return_value = []
        sys.modules["pyttsx3"].init = MagicMock(side_effect=engines)  # type: ignore[attr-defined]
        sp = Speaker(engine="sapi")
        done1 = threading.Event()
        done2 = threading.Event()
        sp.speak("first", on_done=done1.set)
        self.assertTrue(done1.wait(timeout=3.0))
        sp.speak("second", on_done=done2.set)
        self.assertTrue(done2.wait(timeout=3.0))
        sp.stop()
        engines[0].say.assert_called_with("first")
        engines[1].say.assert_called_with("second")

    def test_speak_edge(self):
        communicate_cls, mixer = _install_fake_edge()
        sp = Speaker(engine="edge", voice="en-US-JennyNeural", rate="+0%")
        finished = threading.Event()
        sp.speak("Hello neural", on_done=finished.set)
        self.assertTrue(finished.wait(timeout=5.0))
        sp.stop()
        communicate_cls.assert_called()
        args, kwargs = communicate_cls.call_args
        self.assertEqual(args[0], "Hello neural")
        self.assertEqual(args[1], "en-US-JennyNeural")
        mixer.music.play.assert_called()

    def test_speak_clips_long_text(self):
        sp = Speaker(engine="sapi")
        long = "x" * (tts_mod.MAX_SPEAK_CHARS + 50)
        with patch.object(sp, "_ensure_worker"):
            sp.speak(long)
            text, _cb = sp._q.get_nowait()
        self.assertTrue(text.endswith("..."))
        self.assertLessEqual(len(text), tts_mod.MAX_SPEAK_CHARS + 3)

    def test_speak_wake_ack(self):
        sp = Speaker(wake_ack="yes my lord?")
        with patch.object(sp, "speak") as speak:
            sp.speak_wake_ack()
            speak.assert_called_once_with("yes my lord?", on_done=None)

    def test_edge_rate_helper(self):
        self.assertEqual(tts_mod._edge_rate("+10%"), "+10%")
        self.assertEqual(tts_mod._edge_rate(180), "+0%")
        self.assertEqual(tts_mod._edge_rate(216), "+10%")


if __name__ == "__main__":
    unittest.main()
