"""SessionSetup installs the embedding model after the chat model, and never fails because of it.
The Ollama service is a fake: nothing is pulled."""
import unittest

from desktop_ai_agent.core import embeddings
from desktop_ai_agent.core.session_setup import SessionSetup


class FakeService:
    def __init__(self, fail_on=None):
        self.ensured, self.fail_on = [], fail_on

    def ensure_model(self, name, log=None):
        if name == self.fail_on:
            raise RuntimeError("ollama pull failed (exit 1).")
        self.ensured.append(name)


class TestEmbedModel(unittest.TestCase):
    def setup_for(self, cfg=None, **service):
        fake = FakeService(**service)
        return SessionSetup({"ollama": cfg or {}}, service=fake), fake

    def test_the_default_model_is_ensured(self):
        setup, fake = self.setup_for()
        setup._ensure_embed_model(lambda m: None)
        self.assertEqual(fake.ensured, [embeddings.DEFAULT_MODEL])

    def test_setup_json_can_name_another_model(self):
        setup, fake = self.setup_for({"embed_model": "other-model"})
        setup._ensure_embed_model(lambda m: None)
        self.assertEqual(fake.ensured, ["other-model"])

    def test_a_failed_pull_is_a_log_line_not_an_error(self):
        lines = []
        setup, _ = self.setup_for(fail_on=embeddings.DEFAULT_MODEL)
        setup._ensure_embed_model(lines.append)
        self.assertEqual(len(lines), 1)
        self.assertIn("words only", lines[0])
        self.assertIn(f"ollama pull {embeddings.DEFAULT_MODEL}", lines[0])


if __name__ == "__main__":
    unittest.main()
