"""Tests for absolute pointer_control helpers (hand / sensors)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from desktop_ai_agent.action.tools import mouse_safety, mouse_win32, pointer_control, ui_control

MONITOR = SimpleNamespace(left=0, top=0, right=1920, bottom=1080)


class TestPointerControl(unittest.TestCase):
    def setUp(self):
        ui_control.reset()
        mouse_safety.reset()
        self.addCleanup(ui_control.reset)
        self.addCleanup(mouse_safety.reset)
        self.cursor = []
        self.clicks = []
        self.downs = []
        self.ups = []
        self.wheels = []
        self.pasted = []
        self.keys = []
        self.pointer = (900, 500)
        self.now = 1000.0

        patches = [
            mock.patch.object(mouse_win32, "set_cursor", side_effect=lambda x, y: self.cursor.append((x, y))),
            mock.patch.object(mouse_win32, "cursor_pos", side_effect=lambda: self.pointer),
            mock.patch.object(
                mouse_win32,
                "click",
                side_effect=lambda button, count=1: self.clicks.append(
                    (self.cursor[-1] if self.cursor else self.pointer, button)
                    if count == 1
                    else (self.cursor[-1] if self.cursor else self.pointer, button, count)
                ),
            ),
            mock.patch.object(
                mouse_win32, "button_down", side_effect=lambda button="left": self.downs.append(button)
            ),
            mock.patch.object(
                mouse_win32, "button_up", side_effect=lambda button="left": self.ups.append(button)
            ),
            mock.patch.object(
                mouse_win32, "scroll_wheel", side_effect=lambda dy: self.wheels.append(dy)
            ),
            mock.patch.object(
                mouse_win32, "paste", side_effect=lambda text: self.pasted.append(text)
            ),
            mock.patch.object(
                mouse_win32, "press_enter", side_effect=lambda: self.keys.append("enter")
            ),
            mock.patch.object(mouse_safety, "now", side_effect=lambda: self.now),
            mock.patch.object(mouse_safety, "monitors", side_effect=lambda: [MONITOR]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_move_cursor_at(self):
        result = pointer_control.move_cursor_at(123, 456)
        self.assertEqual(self.cursor, [(123, 456)])
        self.assertEqual((result["x"], result["y"]), (123, 456))

    def test_click_at(self):
        result = pointer_control.click_at(10, 20)
        self.assertEqual(self.clicks, [((10, 20), "left")])
        self.assertEqual(result["clicked"], "absolute")

    def test_mouse_down_up(self):
        pointer_control.click_at(1, 1)
        # Drag down must work even right after a click (no rate limit).
        pointer_control.mouse_down_at(5, 6)
        self.assertEqual(self.downs, ["left"])
        pointer_control.mouse_up_at(7, 8)
        self.assertEqual(self.ups, ["left"])
        self.assertEqual(self.cursor[-1], (7, 8))

    def test_scroll_at(self):
        result = pointer_control.scroll_at(100, 200, 3)
        self.assertEqual(self.wheels, [3])
        self.assertEqual(result["dy"], 3)

    def test_type_at(self):
        result = pointer_control.type_at(50, 60, "hello")
        self.assertEqual(self.pasted, ["hello"])
        self.assertEqual(result["characters"], 5)

    def test_paste_text_no_click(self):
        before = list(self.clicks)
        result = pointer_control.paste_text("hello")
        self.assertEqual(self.pasted, ["hello"])
        self.assertEqual(result["typed"], "focus")
        self.assertEqual(self.clicks, before)

    def test_press_enter(self):
        result = pointer_control.press_enter()
        self.assertEqual(self.keys, ["enter"])
        self.assertEqual(result["pressed"], "enter")

    def test_right_click_here(self):
        self.pointer = (321, 654)
        result = pointer_control.right_click_here()
        self.assertEqual(self.clicks, [((321, 654), "right")])
        self.assertEqual(result["clicked"], "right")
        self.assertEqual((result["x"], result["y"]), (321, 654))

    def test_move_at_edge_does_not_arm_corner_lock(self):
        self.pointer = (0, 0)  # screen corner
        pointer_control.move_cursor_at(0, 0)
        # Still unlocked — a following click_at would arm from corner, but move must not.
        self.pointer = (100, 100)
        pointer_control.click_at(100, 100)
        self.assertEqual(len(self.clicks), 1)

    def test_rate_limit(self):
        pointer_control.click_at(1, 1)
        with self.assertRaisesRegex(PermissionError, "Too fast"):
            pointer_control.click_at(2, 2)

    def test_not_in_registry(self):
        from desktop_ai_agent.action import executor as executor_mod

        ex = executor_mod.default_executor()
        for name in (
            "click_at",
            "type_at",
            "paste_text",
            "press_enter",
            "right_click_here",
            "move_cursor_at",
            "mouse_down_at",
            "mouse_up_at",
            "scroll_at",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, ex.registry)


if __name__ == "__main__":
    unittest.main()
