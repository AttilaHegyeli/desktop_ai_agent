import os
import tempfile
import unittest
from pathlib import Path

from desktop_ai_agent.core import config_loader


class TestDotenv(unittest.TestCase):
    def test_load_and_save_openrouter_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("FOO=bar\n", encoding="utf-8")
            config_loader._dotenv_loaded = False
            os.environ.pop("OPENROUTER_API_KEY", None)
            config_loader.save_openrouter_key_to_env("sk-test-123", path=env_path)
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=sk-test-123", text)
            self.assertIn("FOO=bar", text)
            self.assertEqual(os.environ.get("OPENROUTER_API_KEY"), "sk-test-123")

            loaded = config_loader.load_dotenv(env_path, override=True)
            self.assertEqual(loaded.get("OPENROUTER_API_KEY"), "sk-test-123")
            self.assertEqual(os.environ.get("OPENROUTER_API_KEY"), "sk-test-123")


if __name__ == "__main__":
    unittest.main()
