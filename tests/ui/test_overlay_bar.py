import unittest

from desktop_ai_agent.ui.overlay_bar import (
    OverlayBar,
    clip_line,
    context_meter_state,
    format_exchange_lines,
)


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


class TestContextMeterState(unittest.TestCase):
    def test_empty_context(self):
        frac, short, detail, color = context_meter_state(0, 8192)
        self.assertEqual((frac, short, detail), (0.0, "8.2k left", "0 / 8,192 tokens"))
        self.assertEqual(color, "#5e5ce6")

    def test_colors_shift_with_usage(self):
        self.assertEqual(context_meter_state(6200, 8192)[3], "#ff9f0a")  # > 75 %
        self.assertEqual(context_meter_state(7500, 8192)[3], "#ff3b30")  # > 90 %

    def test_small_remainder_and_clamping(self):
        self.assertEqual(context_meter_state(8000, 8192)[1], "192 left")
        frac, short, _, _ = context_meter_state(99999, 8192)
        self.assertEqual((frac, short), (1.0, "0 left"))


class TestOverlayBarNewChatPrompt(unittest.TestCase):
    """Tk smoke tests for the New-chat composer focus/placeholder bug."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk

        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:
            raise unittest.SkipTest("Tk unavailable") from None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def tearDown(self):
        if getattr(self, "bar", None) is not None:
            self.bar.destroy()
            self.bar = None

    def _make_bar(self) -> OverlayBar:
        self.bar = OverlayBar(self.root, ui={"bar_width": 480, "bar_auto_hide_ms": 0})
        self.bar.set_ready(True)
        self.root.update_idletasks()
        return self.bar

    def test_show_placeholder_while_focused_leaves_empty_field(self):
        bar = self._make_bar()
        bar.focus_prompt()
        self.root.update()
        bar._show_placeholder()
        self.assertFalse(bar._ph_active)
        self.assertEqual(bar.prompt.get(), "")
        self.assertEqual(bar.get_prompt(), "")

    def test_reset_chat_with_focus_allows_real_typing(self):
        bar = self._make_bar()
        bar.set_exchange("previous task", "previous reply")
        bar.focus_prompt()
        self.root.update()
        bar.reset_chat()
        self.root.update()
        self.assertFalse(bar._ph_active)
        self.assertEqual(bar.prompt.get(), "")
        # Simulate the first key after New chat (FocusIn may not re-fire).
        class _E:
            keysym = "h"

        self.assertIsNone(bar._on_prompt_key(_E()))
        bar.prompt.insert(0, "h")
        self.assertEqual(bar.get_prompt(), "h")


if __name__ == "__main__":
    unittest.main()
