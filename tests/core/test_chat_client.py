import json
import unittest
from unittest import mock

from desktop_ai_agent.core.chat_client import ChatClient


class FakeResponse:
    def __init__(self, chunks):
        self._lines = [(json.dumps(c) + "\n").encode() for c in chunks]

    def readline(self):
        return self._lines.pop(0) if self._lines else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patched(chunks):
    """Patch urlopen; capture the JSON payload that was sent."""
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return FakeResponse(chunks)

    return mock.patch("urllib.request.urlopen", fake_urlopen), sent


TOOL_CALL = {"function": {"name": "click", "arguments": {"x": 1, "y": 2}}}
TOOLS = [{"type": "function", "function": {"name": "click"}}]


class TestToolCalling(unittest.TestCase):
    def test_tool_calls_are_captured_and_stored_in_history(self):
        p, _ = patched([
            {"message": {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ])
        c = ChatClient("m")
        c.add_user("click it")
        with p:
            turn = c.chat(tools=TOOLS)
        self.assertEqual(turn.tool_calls, [TOOL_CALL])
        self.assertEqual(c.messages[-1]["role"], "assistant")
        self.assertEqual(c.messages[-1]["tool_calls"], [TOOL_CALL])

    def test_format_dropped_when_tools_given(self):
        p, sent = patched([{"message": {"content": "hi"}, "done": True}])
        with p:
            c = ChatClient("m", response_format="json")
            c.add_user("x")
            c.chat(tools=TOOLS)
        self.assertNotIn("format", sent)
        self.assertEqual(sent["tools"], TOOLS)

    def test_format_kept_for_plain_chat(self):
        p, sent = patched([{"message": {"content": "{}"}, "done": True}])
        with p:
            ChatClient("m", response_format="json").ask("x")
        self.assertEqual(sent["format"], "json")
        self.assertNotIn("tools", sent)

    def test_ask_still_returns_text_and_history(self):
        p, _ = patched([
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": True},
        ])
        c = ChatClient("m")
        with p:
            self.assertEqual(c.ask("hi"), "Hello")
        self.assertEqual([m["role"] for m in c.messages], ["user", "assistant"])

    def test_options_sent_only_when_set(self):
        p, sent = patched([{"message": {"content": "x"}, "done": True}])
        with p:
            ChatClient("m", options={"temperature": 0.2, "num_ctx": 8192}).ask("x")
        self.assertEqual(sent["options"], {"temperature": 0.2, "num_ctx": 8192})
        p2, sent2 = patched([{"message": {"content": "x"}, "done": True}])
        with p2:
            ChatClient("m").ask("x")
        self.assertNotIn("options", sent2)

    def test_text_and_thinking_and_streaming_callback(self):
        p, _ = patched([
            {"message": {"thinking": "hmm"}, "done": False},
            {"message": {"content": "ok"}, "done": True},
        ])
        seen = []
        c = ChatClient("m", think=True)
        c.add_user("x")
        with p:
            turn = c.chat(on_chunk=seen.append)
        self.assertEqual((turn.content, turn.thinking, seen), ("ok", "hmm", ["ok"]))

    def test_message_helpers(self):
        c = ChatClient("m")
        c.add_user("look", images=["BASE64"])
        c.add_tool_result("click", '{"status":"executed"}')
        self.assertEqual(c.messages[0]["images"], ["BASE64"])
        self.assertEqual(c.messages[1], {"role": "tool", "tool_name": "click", "content": '{"status":"executed"}'})

    def test_server_error_raises_runtime_error(self):
        p, _ = patched([{"error": "model not found"}])
        c = ChatClient("m")
        c.add_user("x")
        with p, self.assertRaisesRegex(RuntimeError, "model not found"):
            c.chat()


if __name__ == "__main__":
    unittest.main()