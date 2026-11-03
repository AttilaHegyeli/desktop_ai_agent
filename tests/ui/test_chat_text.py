import json
import unittest

from desktop_ai_agent.ui.chat_text import MAX_CHAT_CHARS, normalize_chat_reply


class TestNormalizeChatReply(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize_chat_reply(None), "")
        self.assertEqual(normalize_chat_reply("  "), "")

    def test_collapses_whitespace(self):
        self.assertEqual(normalize_chat_reply("hi\n\n\nthere"), "hi\n\nthere")
        self.assertEqual(normalize_chat_reply("a  b"), "a b")

    def test_script_json_becomes_labeled_body(self):
        raw = json.dumps({"script": "open_vs_code", "code": "print(1)\n"})
        out = normalize_chat_reply(raw)
        self.assertEqual(out, "Script ready: open_vs_code\n\nprint(1)")

    def test_soft_clip(self):
        huge = "x" * (MAX_CHAT_CHARS + 50)
        out = normalize_chat_reply(huge)
        self.assertEqual(len(out), MAX_CHAT_CHARS)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()
