import os
import tempfile
import unittest
from pathlib import Path

from desktop_ai_agent.core.config_loader import save_env_value
from desktop_ai_agent.remote.call_session import CallController
from desktop_ai_agent.remote.server import RemoteConfigError, RemoteServer


class EnvWriteTests(unittest.TestCase):
    def test_save_env_value_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("OPENROUTER_API_KEY=abc\n", encoding="utf-8")
            save_env_value("REMOTE_TOKEN", "my own password", env)
            self.assertEqual(os.environ["REMOTE_TOKEN"], "my own password")
            text = env.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=abc", text)
            self.assertIn("REMOTE_TOKEN=my own password", text)
            save_env_value("REMOTE_TOKEN", "another password!", env)
            self.assertEqual(env.read_text(encoding="utf-8").count("REMOTE_TOKEN"), 1)
            save_env_value("REMOTE_TOKEN", "", env)
            self.assertNotIn("REMOTE_TOKEN", env.read_text(encoding="utf-8"))
            self.assertNotIn("REMOTE_TOKEN", os.environ)

    def test_multiline_value_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                save_env_value("REMOTE_TOKEN", "a\nb", Path(d) / ".env")


class LiveTokenTests(unittest.TestCase):
    def test_set_token_changes_auth(self):
        call = CallController(
            transcribe=lambda a: "", submit=lambda t: None, synthesize=lambda r: b""
        )
        server = RemoteServer(token="first password!", call=call, port=0)
        self.assertTrue(server.check_token("Bearer first password!"))
        server.set_token("second password!")
        self.assertFalse(server.check_token("Bearer first password!"))
        self.assertTrue(server.check_token("Bearer second password!"))
        with self.assertRaises(RemoteConfigError):
            server.set_token("short")


if __name__ == "__main__":
    unittest.main()
