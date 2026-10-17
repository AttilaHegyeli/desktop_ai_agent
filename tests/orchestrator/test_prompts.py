import unittest

from desktop_ai_agent.action.registry import build_default_registry
from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.orchestrator.prompts import SYSTEM_PROMPT


class TestSystemPrompt(unittest.TestCase):
    def test_tool_named_in_prompt_really_exists(self):
        self.assertIn("provide_script", SYSTEM_PROMPT)
        self.assertIn("provide_script", build_default_registry().names())

    def test_contains_the_offer_question(self):
        self.assertIn("Do you want a script for it?", SYSTEM_PROMPT)

    def test_has_the_eight_rules(self):
        for n in range(1, 9):
            self.assertIn(f"\n{n}. ", SYSTEM_PROMPT)

    def test_prompt_does_not_duplicate_the_tool_list(self):
        # Only provide_script is referenced by name; other actions come from the registry.
        for name in build_default_registry().names():
            if name != "provide_script":
                self.assertNotIn(name, SYSTEM_PROMPT)

    def test_chat_client_sends_it_first_and_keeps_it_after_clear(self):
        c = ChatClient("m", system_prompt=SYSTEM_PROMPT)
        c.add_user("hi")
        c.clear()
        self.assertEqual(c.messages, [{"role": "system", "content": SYSTEM_PROMPT}])


if __name__ == "__main__":
    unittest.main()