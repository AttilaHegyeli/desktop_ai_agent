import json
import unittest
from unittest import mock

from desktop_ai_agent.core.openrouter_client import OpenRouterClient, OpenRouterError


class FakeResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patched(payload: dict):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["headers"] = dict(req.headers)
        sent["body"] = json.loads(req.data.decode())
        return FakeResponse(payload)

    return mock.patch("urllib.request.urlopen", fake_urlopen), sent


class TestOpenRouterClient(unittest.TestCase):
    def test_images_and_tool_results_map_to_openai_shape(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "wait", "arguments": '{"seconds": 1}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        p, sent = patched(payload)
        c = OpenRouterClient("openai/gpt-4o", "sk-test", system="sys", context_tokens=128000)
        c.add_user("look", images=["abc123"])
        with p:
            turn = c.chat(tools=[{"type": "function", "function": {"name": "wait"}}])
        self.assertEqual(turn.tool_calls[0]["function"]["name"], "wait")
        self.assertEqual(c.context_used, 12)
        user_msg = sent["body"]["messages"][1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        self.assertEqual(user_msg["content"][1]["type"], "image_url")
        self.assertIn("base64,abc123", user_msg["content"][1]["image_url"]["url"])

        c.add_tool_result("wait", '{"ok": true}')
        p2, sent2 = patched(
            {
                "choices": [{"message": {"role": "assistant", "content": "done"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 1},
            }
        )
        with p2:
            c.chat()
        tool_msg = next(m for m in sent2["body"]["messages"] if m["role"] == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_1")
        self.assertNotIn("tool_name", tool_msg)

    def test_http_error_is_openrouter_error(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "rate", hdrs=None, fp=mock.Mock(read=lambda: b"slow down")
            )

        c = OpenRouterClient("m", "k", system="")
        c.add_user("hi")
        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(OpenRouterError) as ctx:
                c.chat()
        self.assertEqual(ctx.exception.status, 429)
        self.assertTrue(ctx.exception.retriable)
        self.assertFalse(ctx.exception.skip_api_fallback)

    def test_key_limit_403_skips_api_fallback(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url,
                403,
                "forbidden",
                hdrs=None,
                fp=mock.Mock(
                    read=lambda: b'{"error":{"message":"$0.00 used","metadata":{"limit_source":"openrouter_key_limit"}}}'
                ),
            )

        c = OpenRouterClient("m", "k", system="")
        c.add_user("hi")
        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(OpenRouterError) as ctx:
                c.chat()
        self.assertEqual(ctx.exception.status, 403)
        self.assertTrue(ctx.exception.retriable)
        self.assertTrue(ctx.exception.skip_api_fallback)



if __name__ == "__main__":
    unittest.main()
