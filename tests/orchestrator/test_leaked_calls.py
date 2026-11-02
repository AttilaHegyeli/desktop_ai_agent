"""Gemma sometimes writes a tool call as text ("call:wait{seconds:5}<tool_call|>").
The loop must run it instead of showing it as the final answer."""
import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatClient, ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import leaked_tool_calls, run_task


class ScriptedClient(ChatClient):
    """A real ChatClient (real history) whose model replies are scripted."""

    def __init__(self, turns):
        super().__init__("m", system="sys")
        self.turns = list(turns)
        self.sent = []

    def chat(self, tools=None, on_chunk=None):
        self.sent.append([dict(m) for m in self.messages])
        turn = self.turns.pop(0)
        entry = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            entry["tool_calls"] = turn.tool_calls
        self.messages.append(entry)
        return turn


def make_executor():
    specs = [
        ActionSpec("screenshot", "shot", Risk.LOW),
        ActionSpec("wait", "wait", Risk.LOW, (Param("seconds", "number", "s", minimum=0, maximum=30),)),
        ActionSpec("open_app", "open", Risk.MEDIUM, (Param("name", "string", "n"),)),
        ActionSpec("type_text", "type", Risk.MEDIUM, (
            Param("element", "integer", "e", minimum=1), Param("text", "string", "t"),
        )),
    ]
    ran = []
    handlers = {s.name: (lambda n: lambda a: ran.append((n, a)) or "ok")(s.name) for s in specs}
    return Executor(ActionRegistry(specs), handlers), ran


def parsed(text):
    reg = make_executor()[0].registry
    return [(c["function"]["name"], c["function"]["arguments"]) for c in leaked_tool_calls(text, reg)]


class TestParse(unittest.TestCase):
    def test_the_real_leaks(self):
        self.assertEqual(parsed("call:screenshot{}<tool_call|><|tool_response>"), [("screenshot", {})])
        self.assertEqual(parsed("call:wait{seconds:5}<tool_call|>"), [("wait", {"seconds": 5})])

    def test_gemma_strings_and_json(self):
        self.assertEqual(parsed('<|tool_call>call:open_app{name:<|"|>spotify<|"|>}<tool_call|>'),
                         [("open_app", {"name": "spotify"})])
        self.assertEqual(parsed('call:open_app{"name": "spotify"}'), [("open_app", {"name": "spotify"})])
        self.assertEqual(parsed("call:open_app{name:spotify}"), [("open_app", {"name": "spotify"})])

    def test_braces_and_colons_inside_strings(self):
        self.assertEqual(
            parsed('call:type_text{element:2,text:<|"|>a {b}: c, d<|"|>}'),
            [("type_text", {"element": 2, "text": "a {b}: c, d"})],
        )

    def test_several_calls(self):
        self.assertEqual(parsed("call:wait{seconds:1}<tool_call|>call:screenshot{}"),
                         [("wait", {"seconds": 1}), ("screenshot", {})])

    def test_ignores_unknown_names_and_garbage(self):
        self.assertEqual(parsed("call:format_disk{}"), [])  # not in the registry
        self.assertEqual(parsed("call:wait{seconds:"), [])  # unclosed
        self.assertEqual(parsed("You can call:wait if you like"), [])  # no arguments block
        self.assertEqual(parsed("Spotify is open."), [])


class TestLoopRecovers(unittest.TestCase):
    def test_spotify_case_waits_instead_of_ending(self):
        ex, ran = make_executor()
        client = ScriptedClient([
            ChatTurn(tool_calls=[{"function": {"name": "open_app", "arguments": {"name": "spotify"}}}]),
            ChatTurn(content="call:wait{seconds:5}<tool_call|>"),
            ChatTurn(content="Spotify is open."),
        ])
        events = []
        res = run_task(client, ex, "open spotify. wait.", auto_screenshot=False,
                       on_event=lambda k, d: events.append((k, d)))
        self.assertEqual((res.status, res.text), ("done", "Spotify is open."))
        self.assertEqual([n for n, _ in ran], ["open_app", "wait"])
        self.assertEqual(ran[1][1], {"seconds": 5})
        self.assertIn("recovered", [k for k, _ in events])
        self.assertNotIn(("final", "call:wait{seconds:5}<tool_call|>"), events)
        # History: the leaked text is gone and the call is a real one with an answer.
        leaked = client.sent[2][-2]
        self.assertEqual(leaked["role"], "assistant")
        self.assertEqual(leaked["content"], "")
        self.assertEqual(leaked["tool_calls"][0]["function"]["name"], "wait")
        self.assertEqual(client.sent[2][-1]["tool_name"], "wait")

    def test_unparseable_text_is_still_a_normal_answer(self):
        ex, ran = make_executor()
        client = ScriptedClient([ChatTurn(content="call:wait{seconds:")])
        res = run_task(client, ex, "go", auto_screenshot=False)
        self.assertEqual((res.status, res.text), ("done", "call:wait{seconds:"))
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
