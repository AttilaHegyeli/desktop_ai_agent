import unittest

from desktop_ai_agent.action.registry import build_default_registry
from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.orchestrator.prompts import SYSTEM_PROMPT


# Tools named in the prompt for routing; everything else comes from the registry only.
_NAMED_IN_PROMPT = frozenset({"provide_script", "web_search", "web_open"})


class TestSystemPrompt(unittest.TestCase):
    def test_tool_named_in_prompt_really_exists(self):
        names = build_default_registry().names()
        for name in _NAMED_IN_PROMPT:
            self.assertIn(name, SYSTEM_PROMPT)
            self.assertIn(name, names)

    def test_contains_the_offer_question(self):
        self.assertIn("Do you want a script for it?", SYSTEM_PROMPT)

    def test_has_the_numbered_rules(self):
        for n in range(1, 14):
            self.assertIn(f"\n{n}. ", SYSTEM_PROMPT)

    def test_prompt_does_not_duplicate_the_tool_list(self):
        # Only routing-critical tools are referenced by name.
        for name in build_default_registry().names():
            if name not in _NAMED_IN_PROMPT:
                self.assertNotIn(name, SYSTEM_PROMPT)

    def test_chat_client_sends_it_first_and_keeps_it_after_clear(self):
        c = ChatClient("m", system=SYSTEM_PROMPT)
        c.add_user("hi")
        c.clear()
        self.assertEqual(c.messages, [{"role": "system", "content": SYSTEM_PROMPT}])


if __name__ == "__main__":
    unittest.main()