"""Tests for web_open. No real browser: webbrowser.open is always mocked."""
import unittest
from unittest import mock

from desktop_ai_agent.action import handlers
from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.action.registry import ValidationError, build_default_registry
from desktop_ai_agent.action.tools import web_open as mod


class TestOpenHelper(unittest.TestCase):
    def test_https_opens(self):
        opened = []
        out = mod.open_url("https://github.com/octocat", open_fn=opened.append)
        self.assertEqual(opened, ["https://github.com/octocat"])
        self.assertEqual(out, {"opened": "https://github.com/octocat"})

    def test_http_allowed(self):
        opened = []
        mod.open_url("http://example.com/path?q=1", open_fn=opened.append)
        self.assertEqual(opened, ["http://example.com/path?q=1"])

    def test_rejects_file_scheme(self):
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            mod.open_url("file:///C:/secret.txt", open_fn=lambda u: True)

    def test_rejects_javascript(self):
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            mod.open_url("javascript:alert(1)", open_fn=lambda u: True)

    def test_rejects_empty_and_hostless(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            mod.open_url("  ", open_fn=lambda u: True)
        with self.assertRaisesRegex(ValueError, "must include a host"):
            mod.open_url("https://", open_fn=lambda u: True)


class TestWebOpenHandler(unittest.TestCase):
    def test_handler_uses_helper(self):
        with mock.patch.object(
            handlers, "_open_url", return_value={"opened": "https://youtube.com"}
        ) as m:
            out = handlers.web_open({"url": "https://youtube.com"})
        m.assert_called_once_with("https://youtube.com")
        self.assertEqual(out["opened"], "https://youtube.com")

    def test_registry_and_executor(self):
        spec = build_default_registry().get("web_open")
        self.assertEqual(spec.risk.value, "medium")
        clean = spec.validate({"url": "https://www.youtube.com/results?search_query=lofi"})
        self.assertIn("youtube.com", clean["url"])

        with mock.patch.object(
            handlers, "_open_url", return_value={"opened": clean["url"]}
        ):
            res = default_executor().run(
                [{"name": "web_open", "arguments": {"url": clean["url"]}}]
            )
        self.assertTrue(res[0].ok)

    def test_missing_url_rejected(self):
        with self.assertRaises(ValidationError):
            build_default_registry().get("web_open").validate({})


if __name__ == "__main__":
    unittest.main()
