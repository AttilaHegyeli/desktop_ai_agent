import unittest

from desktop_ai_agent.ui.overlay_bar import clip_line, format_exchange_lines


class TestFormatExchangeLines(unittest.TestCase):
    def test_waiting_uses_ellipsis(self):
        me, ai = format_exchange_lines("Open Vs Code", None)
        self.assertEqual(me, "me: Open Vs Code")
        self.assertEqual(ai, "…")

    def test_reply_pair(self):
        me, ai = format_exchange_lines("Open Vs Code", "The Visual studio code is open")
        self.assertEqual(me, "me: Open Vs Code")
        self.assertEqual(ai, "The Visual studio code is open")

    def test_strips_whitespace(self):
        me, ai = format_exchange_lines("  hi  ", "  there  ")
        self.assertEqual(me, "me: hi")
        self.assertEqual(ai, "there")

    def test_keeps_multiline_ai_for_scroll_panel(self):
        me, ai = format_exchange_lines("q", "line1\n\nline2")
        self.assertEqual(me, "me: q")
        self.assertEqual(ai, "line1\n\nline2")

    def test_waiting_marker_is_short(self):
        # Compact thinking UI uses a short placeholder until typewriter starts.
        _, ai = format_exchange_lines("task", None)
        self.assertLessEqual(len(ai), 3)


class TestClipLine(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(clip_line("hello"), "hello")

    def test_collapses_newlines(self):
        self.assertEqual(clip_line("a\nb"), "a b")

    def test_long_clipped(self):
        text = "x" * 200
        out = clip_line(text, max_chars=20)
        self.assertEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()
