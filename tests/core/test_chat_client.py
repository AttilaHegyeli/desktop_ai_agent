import json
import unittest
from unittest import mock

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.response_format import AGENT_JSON_SCHEMA, agent_system_prompt


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
        c = ChatClient("m", system="")
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

    def test_default_json_schema_is_also_dropped_when_tools_given(self):
        p, sent = patched([{"message": {"content": "hi"}, "done": True}])
        with p:
            c = ChatClient("m")  # default response_format = their JSON schema
            c.add_user("x")
            c.chat(tools=TOOLS)
        self.assertNotIn("format", sent)

    def test_chat_with_tools_does_not_normalize_the_reply(self):
        p, _ = patched([{"message": {"content": "I cannot do that. Want a script?"}, "done": True}])
        c = ChatClient("m", system="")
        c.add_user("x")
        with p:
            turn = c.chat(tools=TOOLS)
        self.assertEqual(turn.content, "I cannot do that. Want a script?")

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
        c = ChatClient("m", think=True, system="")
        c.add_user("x")
        with p:
            turn = c.chat(on_chunk=seen.append)
        self.assertEqual((turn.content, turn.thinking, seen), ("ok", "hmm", ["ok"]))

    def test_message_helpers(self):
        c = ChatClient("m", system="")
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


class TestLegacyJsonPathStillWorks(unittest.TestCase):
    """The other branch's behaviour must survive the merge."""

    def test_default_format_is_their_json_schema(self):
        p, sent = patched([{"message": {"content": "{}"}, "done": True}])
        with p:
            ChatClient("m").ask("x")
        self.assertEqual(sent["format"], AGENT_JSON_SCHEMA)
        self.assertNotIn("tools", sent)

    def test_explicit_string_format_is_kept(self):
        p, sent = patched([{"message": {"content": "{}"}, "done": True}])
        with p:
            ChatClient("m", response_format="json").ask("x")
        self.assertEqual(sent["format"], "json")

    def test_ask_normalizes_messy_replies(self):
        messy = '```json\n{"script": "open_vs_code", "code": "print(1)"}\n```'
        p, _ = patched([{"message": {"content": messy}, "done": True}])
        with p:
            reply = ChatClient("m").ask("open vs code")
        self.assertEqual(json.loads(reply), {"script": "open_vs_code", "code": "print(1)\n"})

    def test_normalize_false_returns_raw_text(self):
        p, _ = patched([
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": True},
        ])
        c = ChatClient("m", normalize=False, system="")
        with p:
            self.assertEqual(c.ask("hi"), "Hello")
        self.assertEqual([m["role"] for m in c.messages], ["user", "assistant"])

    def test_ask_stream_streams_chunks_and_returns_normalized(self):
        p, _ = patched([
            {"message": {"content": '{"script": "open_x",'}, "done": False},
            {"message": {"content": ' "code": "pass"}'}, "done": True},
        ])
        seen = []
        with p:
            reply = ChatClient("m").ask_stream("open x", seen.append)
        self.assertEqual(len(seen), 2)
        self.assertEqual(json.loads(reply)["script"], "open_x")


class TestSystemPrompt(unittest.TestCase):
    def test_default_is_their_scriptwriter_prompt(self):
        self.assertEqual(
            ChatClient("m").messages, [{"role": "system", "content": agent_system_prompt()}]
        )

    def test_empty_system_means_no_system_message(self):
        self.assertEqual(ChatClient("m", system="").messages, [])

    def test_custom_system_is_first_message_and_survives_clear(self):
        c = ChatClient("m", system="be brief")
        self.assertEqual(c.messages, [{"role": "system", "content": "be brief"}])
        c.add_user("hi")
        c.clear()
        self.assertEqual(c.messages, [{"role": "system", "content": "be brief"}])

    def test_system_is_sent_to_ollama(self):
        p, sent = patched([{"message": {"content": "x"}, "done": True}])
        with p:
            c = ChatClient("m", system="be brief")
            c.add_user("hi")
            c.chat()
        self.assertEqual(sent["messages"][0], {"role": "system", "content": "be brief"})


if __name__ == "__main__":
    unittest.main()