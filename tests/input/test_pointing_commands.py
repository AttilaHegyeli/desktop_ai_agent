"""Pointing / voice-shortcut parser tests."""
from __future__ import annotations

import unittest

from desktop_ai_agent.input.pointing_commands import (
    parse_enter_command,
    parse_right_click_command,
    parse_type_command,
)


class TestParseTypeCommand(unittest.TestCase):
    def test_type_and_write(self):
        self.assertEqual(parse_type_command("type hello").text, "hello")
        self.assertEqual(parse_type_command("write hello world").text, "hello world")
        self.assertEqual(parse_type_command("Type Hello Here").text, "Hello")
        self.assertEqual(parse_type_command("type meet at 3 there").text, "meet at 3")

    def test_rejects_non_type(self):
        self.assertIsNone(parse_type_command("click here"))
        self.assertIsNone(parse_type_command("open notepad"))
        self.assertIsNone(parse_type_command("type"))
        self.assertIsNone(parse_type_command("type here"))
        self.assertIsNone(parse_type_command(""))
        self.assertIsNone(parse_type_command("enter"))


class TestParseEnterCommand(unittest.TestCase):
    def test_enter_phrases(self):
        for phrase in ("enter", "press enter", "Press Enter", "hit enter", "return"):
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(parse_enter_command(phrase))

    def test_rejects_other(self):
        self.assertIsNone(parse_enter_command("type enter"))
        self.assertIsNone(parse_enter_command("click"))
        self.assertIsNone(parse_enter_command(""))


class TestParseRightClickCommand(unittest.TestCase):
    def test_click_phrases(self):
        for phrase in ("click", "right click", "Click Here", "right click there"):
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(parse_right_click_command(phrase))

    def test_rejects_other(self):
        self.assertIsNone(parse_right_click_command("click the button"))
        self.assertIsNone(parse_right_click_command("enter"))
        self.assertIsNone(parse_right_click_command(""))


if __name__ == "__main__":
    unittest.main()
