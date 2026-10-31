"""The agent loop pushes the model once when it announces an action instead of doing it."""
import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import NUDGE, promises_action, run_task


class Client:
    def __init__(self, turns):
        self.turns = list(turns)
        self.user = []

    def add_user(self, text, images=None):
        self.user.append(text)

    def add_tool_result(self, name, content):
        pass

    def chat(self, tools=None, on_chunk=None):
        return self.turns.pop(0)


def peek():
    return ChatTurn(tool_calls=[{"function": {"name": "peek", "arguments": {}}}])


def executor():
    ran = []
    reg = ActionRegistry([ActionSpec("peek", "look", Risk.LOW)])
    return Executor(reg, {"peek": lambda a: ran.append(1) or "ok"}), ran


class TestPromisesAction(unittest.TestCase):
    def test_promises(self):
        for text in ("I will now type 'aaa' into it.", "I'll click the search box.", "Now I type it.",
                     "Next, I click Save.", "I am going to open it.", "Let me click that."):
            with self.subTest(text=text):
                self.assertTrue(promises_action(text))

    def test_reports_and_questions(self):
        for text in ("Done.", "I typed 'aaa' into the search box.", "Let me know if you need more.",
                     "Which one do you mean?", "Clicked.", "", "Twill is a fabric."):
            with self.subTest(text=text):
                self.assertFalse(promises_action(text))


class TestNudge(unittest.TestCase):
    def test_announce_after_tools_gets_one_nudge_then_acts(self):
        ex, ran = executor()
        client = Client([peek(), ChatTurn(content="I will now type it."), peek(), ChatTurn(content="Typed.")])
        events = []
        res = run_task(client, ex, "go", auto_screenshot=False, on_event=lambda k, d: events.append(k))
        self.assertEqual((res.status, res.text), ("done", "Typed."))
        self.assertEqual(len(ran), 2)
        self.assertEqual(client.user, ["go", NUDGE])
        self.assertIn("nudge", events)

    def test_only_one_nudge_per_task(self):
        ex, _ = executor()
        client = Client([peek(), ChatTurn(content="I will type it."), ChatTurn(content="I will type it, promise.")])
        res = run_task(client, ex, "go", auto_screenshot=False)
        self.assertEqual((res.status, res.text), ("done", "I will type it, promise."))
        self.assertEqual(client.user.count(NUDGE), 1)

    def test_no_nudge_without_earlier_tool_use(self):
        ex, _ = executor()
        client = Client([ChatTurn(content="I will need more details. Which folder?")])
        res = run_task(client, ex, "go", auto_screenshot=False)
        self.assertEqual(res.text, "I will need more details. Which folder?")
        self.assertNotIn(NUDGE, client.user)

    def test_no_nudge_for_a_plain_report(self):
        ex, _ = executor()
        client = Client([peek(), ChatTurn(content="Typed 'aaa' into the search box.")])
        res = run_task(client, ex, "go", auto_screenshot=False)
        self.assertEqual(res.rounds, 2)
        self.assertNotIn(NUDGE, client.user)


if __name__ == "__main__":
    unittest.main()
