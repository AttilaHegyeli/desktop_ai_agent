import unittest

from desktop_ai_agent.orchestrator.agent_loop import needs_screen_vision


class TestNeedsScreenVision(unittest.TestCase):
    def test_vision_phrases(self):
        for text in (
            "what do you see?",
            "What do you see on my computer",
            "tell me what you see",
            "tell me what is there",
            "what is there",
            "what's playing",
            "tell me the first song",
            "which tab is open",
            "can you see this",
            "read this",
            "open spotify and tell me what is there",
            "open chrome and describe what you see",
            "open spotify and tell me the first song",
            "look at my screen",
            "describe the screen",
            "take a screenshot",
            "show me my desktop",
            "check the window",
            "current screen please",
        ):
            self.assertTrue(needs_screen_vision(text), text)

    def test_non_vision_phrases(self):
        for text in (
            "hi",
            "write me a script",
            "wait 2 seconds",
            "open spotify",
            "thanks",
            "search for weather",
            "what is the capital of France",
        ):
            self.assertFalse(needs_screen_vision(text), text)


if __name__ == "__main__":
    unittest.main()
