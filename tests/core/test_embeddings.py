"""Tests for core/embeddings.py. Ollama is never called: `_post` is a fake."""
import math
import unittest
import urllib.error
from unittest import mock

from desktop_ai_agent.core import embeddings as em
from desktop_ai_agent.core.embeddings import settings as real_settings  # before conftest's patch


class FakeOllama:
    def __init__(self, dim=3, error=None, wrong_count=False):
        self.dim, self.error, self.wrong_count = dim, error, wrong_count
        self.bodies = []

    def __call__(self, url, body, timeout):
        self.bodies.append((url, body))
        if self.error is not None:
            raise self.error
        count = len(body["input"]) - (1 if self.wrong_count else 0)
        return {"embeddings": [[3.0, 4.0, 0.0][: self.dim] for _ in range(count)]}


class TestEmbed(unittest.TestCase):
    def embed(self, fake, texts, **kw):
        with mock.patch.object(em, "_post", fake):
            return em.embed(texts, model="m", host="http://h", **kw)

    def test_cpu_only_batched_and_normalized(self):
        fake = FakeOllama()
        with mock.patch.object(em, "BATCH", 2):
            vectors = self.embed(fake, ["a", "b", "c"])
        self.assertEqual(len(vectors), 3)
        self.assertEqual([len(b["input"]) for _, b in fake.bodies], [2, 1])
        url, body = fake.bodies[0]
        self.assertEqual(url, "http://h/api/embed")
        self.assertEqual(body["options"], {"num_gpu": 0})
        self.assertEqual(body["model"], "m")
        self.assertIn("keep_alive", body)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vectors[0])), 1.0)

    def test_nothing_to_embed(self):
        self.assertEqual(self.embed(FakeOllama(), []), [])

    def test_missing_model_says_how_to_pull_it(self):
        error = urllib.error.HTTPError("u", 404, "not found", {}, None)
        with self.assertRaisesRegex(em.EmbeddingError, "ollama pull m"):
            self.embed(FakeOllama(error=error), ["a"])

    def test_ollama_down(self):
        with self.assertRaisesRegex(em.EmbeddingError, "not reachable"):
            self.embed(FakeOllama(error=urllib.error.URLError("refused")), ["a"])

    def test_wrong_answer(self):
        with self.assertRaisesRegex(em.EmbeddingError, "no embeddings"):
            self.embed(FakeOllama(wrong_count=True), ["a", "b"])

    def test_stop_between_batches(self):
        with self.assertRaisesRegex(em.EmbeddingError, "stopped"):
            self.embed(FakeOllama(), ["a"], should_stop=lambda: True)

    def test_settings_from_setup_json(self):
        with mock.patch("desktop_ai_agent.core.config_loader.load_setup",
                        return_value={"ollama": {"host": "http://x:1/", "embed_model": "e"}}):
            self.assertEqual(real_settings(), ("e", "http://x:1"))
        with mock.patch("desktop_ai_agent.core.config_loader.load_setup", return_value={"ollama": {}}):
            self.assertEqual(real_settings(), (em.DEFAULT_MODEL, em.DEFAULT_HOST))
        with mock.patch("desktop_ai_agent.core.config_loader.load_setup", side_effect=OSError):
            self.assertEqual(real_settings(), (em.DEFAULT_MODEL, em.DEFAULT_HOST))


class TestVectors(unittest.TestCase):
    def test_pack_and_unpack(self):
        vectors = [em.normalize([1, 2, 2]), em.normalize([0, 0, 5])]
        back = em.unpack(em.pack(vectors), 3)
        self.assertEqual(len(back), 2)
        self.assertAlmostEqual(em.dot(back[0], vectors[0]), 1.0, places=5)
        self.assertAlmostEqual(em.dot(back[1], [0, 0, 1]), 1.0, places=5)

    def test_unpack_wrong_size(self):
        with self.assertRaises(ValueError):
            em.unpack(em.pack([[1.0, 2.0, 3.0]]), 2)

    def test_zero_vector_stays_zero(self):
        self.assertEqual(em.normalize([0, 0]), [0.0, 0.0])
