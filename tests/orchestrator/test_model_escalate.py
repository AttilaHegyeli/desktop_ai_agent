import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.core.openrouter_client import OpenRouterError
from desktop_ai_agent.orchestrator.agent_loop import run_task


class EscalatingClient:
    def __init__(self):
        self.context_used = 0
        self.context_limit = 100
        self.fails = 1
        self.escalated = False
        self.notes = []

    def add_user(self, text, images=None):
        self.notes.append(text)

    def add_tool_result(self, name, content):
        pass

    def clear(self):
        self.context_used = 0

    def chat(self, tools=None, on_chunk=None):
        if self.fails > 0:
            self.fails -= 1
            raise OpenRouterError("rate limited", status=429, retriable=True)
        return ChatTurn(content="recovered after switch")

    def escalate(self, handoff_note, *, reason="context", skip_api_fallback=False):
        self.escalated = True
        self.notes.append(handoff_note)
        self.context_used = 0
        return {
            "label": "Local · gemma4 (fallback)",
            "tier": "local",
            "reason": reason,
            "skip_api_fallback": skip_api_fallback,
        }


def executor():
    reg = ActionRegistry([ActionSpec("peek", "look", Risk.LOW)])
    return Executor(reg, {"peek": lambda a: "ok"})


class TestModelEscalate(unittest.TestCase):
    def test_retriable_api_error_escalates_and_continues(self):
        client = EscalatingClient()
        events = []
        result = run_task(
            client,
            executor(),
            "hi",
            auto_screenshot=False,
            on_event=lambda k, d: events.append(k),
        )
        self.assertEqual(result.status, "done")
        self.assertTrue(client.escalated)
        self.assertIn("model_switch", events)
        self.assertIn("handoff", events)


if __name__ == "__main__":
    unittest.main()
