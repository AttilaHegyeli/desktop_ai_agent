import unittest

from desktop_ai_agent.orchestrator.agent_loop import needs_screen_vision


class TestNeedsScreenVision(unittest.TestCase):
    def test_vision_phrases(self):
        for text in (
            "what do you see?",
            "What do you see on my computer",
            "look at my screen",
            "describe the screen",
            "take a screenshot",
            "mi van a képernyőn",
            "mit látsz",
        ):
            self.assertTrue(needs_screen_vision(text), text)

    def test_non_vision_phrases(self):
        for text in (
            "hi",
            "write me a script",
            "wait 2 seconds",
            "open spotify",
            "thanks",
        ):
            self.assertFalse(needs_screen_vision(text), text)


if __name__ == "__main__":
    unittest.main()
