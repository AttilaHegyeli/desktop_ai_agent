"""Tests for web_search. No real network: DDGS is always mocked."""
import unittest
from unittest import mock

from desktop_ai_agent.action import handlers
from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.action.registry import ValidationError, build_default_registry
from desktop_ai_agent.action.tools import web_search as mod


class TestSearchHelper(unittest.TestCase):
    def test_normalizes_ddgs_hits(self):
        def fake(query, max_results=3):
            self.assertEqual(query, "budapest weather")
            self.assertEqual(max_results, 2)
            return [
                {"title": "Weather", "href": "https://example.com/w", "body": "Sunny 20C"},
                {"title": "Other", "url": "https://example.com/o", "snippet": "Clouds"},
            ]

        out = mod.search_web("budapest weather", max_results=2, search_fn=fake)
        self.assertEqual(out["query"], "budapest weather")
        self.assertEqual(
            out["results"],
            [
                {"title": "Weather", "url": "https://example.com/w", "snippet": "Sunny 20C"},
                {"title": "Other", "url": "https://example.com/o", "snippet": "Clouds"},
            ],
        )

    def test_empty_query_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            mod.search_web("  ", search_fn=lambda *a, **k: [])

    def test_caps_max_results(self):
        seen = {}

        def fake(query, max_results=3):
            seen["max_results"] = max_results
            return []

        mod.search_web("q", max_results=99, search_fn=fake)
        self.assertEqual(seen["max_results"], 5)

    def test_empty_results_ok(self):
        out = mod.search_web("obscure", search_fn=lambda *a, **k: [])
        self.assertEqual(out, {"query": "obscure", "results": []})

    def test_ddgs_exception_is_wrapped(self):
        class FakeDDGS:
            def text(self, query, max_results=None):
                raise OSError("connection reset")

        fake_mod = mock.MagicMock()
        fake_mod.DDGS = FakeDDGS
        with mock.patch.dict("sys.modules", {"ddgs": fake_mod}):
            with self.assertRaisesRegex(RuntimeError, "web search failed"):
                mod.search_web("x")


class TestWebSearchHandler(unittest.TestCase):
    def test_handler_uses_helper(self):
        with mock.patch.object(
            handlers,
            "_search_web",
            return_value={"query": "hi", "results": []},
        ) as m:
            out = handlers.web_search({"query": "hi"})
        m.assert_called_once_with("hi", max_results=3)
        self.assertEqual(out["query"], "hi")

    def test_handler_passes_max_results(self):
        with mock.patch.object(
            handlers,
            "_search_web",
            return_value={"query": "hi", "results": []},
        ) as m:
            handlers.web_search({"query": "hi", "max_results": 5})
        m.assert_called_once_with("hi", max_results=5)

    def test_registry_and_executor(self):
        spec = build_default_registry().get("web_search")
        self.assertEqual(spec.risk.value, "low")
        clean = spec.validate({"query": "weather today"})
        self.assertEqual(clean, {"query": "weather today"})

        with mock.patch.object(
            handlers,
            "_search_web",
            return_value={"query": "weather today", "results": []},
        ):
            res = default_executor().run(
                [{"name": "web_search", "arguments": {"query": "weather today"}}]
            )
        self.assertTrue(res[0].ok)

    def test_query_too_long_rejected(self):
        with self.assertRaises(ValidationError):
            build_default_registry().get("web_search").validate({"query": "x" * 201})


if __name__ == "__main__":
    unittest.main()
