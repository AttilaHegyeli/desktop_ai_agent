import json
import unittest

from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import run_task


class FakeClient:
    """Scripted model. A call beyond the script raises IndexError (= an unwanted extra model call)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.tool_msgs = []

    def add_user(self, text, images=None):
        pass

    def add_tool_result(self, name, content):
        self.tool_msgs.append((name, json.loads(content)))

    def chat(self, tools=None, on_chunk=None):
        return self.turns.pop(0)


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def turn(*calls):
    return ChatTurn(tool_calls=list(calls))


GOOD = {"script": "print hello", "code": "print('hello')\n"}


def _run(client, text, **kwargs):
    """Script-loop tests disable auto-screenshot (no real capture / mss)."""
    kwargs.setdefault("auto_screenshot", False)
    return run_task(client, default_executor(), text, **kwargs)


class TestProvideScriptEndsTurn(unittest.TestCase):
    def test_stops_after_provide_script_without_another_model_call(self):
        client = FakeClient([turn(call("provide_script", **GOOD))])  # only ONE turn queued
        res = _run(client, "write me a script")
        self.assertEqual((res.status, res.rounds), ("done", 1))
        self.assertEqual(json.loads(res.text), GOOD)

    def test_output_is_compact_single_line_json(self):
        client = FakeClient([turn(call("provide_script", **GOOD))])
        res = _run(client, "x")
        self.assertEqual(res.text, '{"script": "print hello", "code": "print(\'hello\')\\n"}')
        self.assertNotIn("\n", res.text)

    def test_tool_result_still_recorded_in_history(self):
        client = FakeClient([turn(call("provide_script", **GOOD))])
        _run(client, "x")
        self.assertEqual(client.tool_msgs[0][0], "provide_script")
        self.assertEqual(client.tool_msgs[0][1]["status"], "executed")

    def test_final_event_carries_the_json(self):
        events = []
        client = FakeClient([turn(call("provide_script", **GOOD))])
        res = _run(client, "x", on_event=lambda k, d: events.append((k, d)))
        self.assertEqual(events[-1], ("final", res.text))

    def test_rejected_provide_script_is_retried(self):
        client = FakeClient([
            turn(call("provide_script", script="no code field")),
            turn(call("provide_script", **GOOD)),
        ])
        res = _run(client, "x")
        self.assertEqual((res.status, res.rounds), ("done", 2))
        self.assertEqual(client.tool_msgs[0][1]["status"], "rejected")
        self.assertEqual(json.loads(res.text), GOOD)

    def test_mixed_plan_runs_everything_then_stops(self):
        client = FakeClient([turn(call("wait", seconds=0), call("provide_script", **GOOD))])
        res = _run(client, "x")
        self.assertEqual((res.status, res.rounds), ("done", 1))
        self.assertEqual([m[1]["status"] for m in client.tool_msgs], ["executed", "executed"])
        self.assertEqual(json.loads(res.text), GOOD)

    def test_loop_continues_when_a_step_in_the_same_plan_fails(self):
        # provide_script runs, then screenshot errors -> ends_turn does not finish the turn
        from unittest.mock import patch

        client = FakeClient([
            turn(call("provide_script", **GOOD), call("screenshot")),
            ChatTurn(content="Sorry, the screenshot failed."),
        ])
        with patch(
            "desktop_ai_agent.action.handlers.capture_screen",
            side_effect=RuntimeError("capture failed"),
        ):
            res = _run(client, "x")
        self.assertEqual((res.status, res.rounds), ("done", 2))
        self.assertEqual(res.text, "Sorry, the screenshot failed.")

    def test_invalid_plan_with_provide_script_does_not_stop_the_loop(self):
        client = FakeClient([
            turn(call("provide_script", **GOOD), call("wait", seconds=999)),
            ChatTurn(content="fixed"),
        ])
        res = _run(client, "x")
        self.assertEqual((res.status, res.rounds, res.text), ("done", 2, "fixed"))

    def test_normal_actions_still_return_to_the_model(self):
        client = FakeClient([turn(call("wait", seconds=0)), ChatTurn(content="done waiting")])
        res = _run(client, "x")
        self.assertEqual((res.rounds, res.text), (2, "done waiting"))


if __name__ == "__main__":
    unittest.main()