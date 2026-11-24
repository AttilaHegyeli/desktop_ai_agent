"""Registry entries and handlers of the browser tools (browser_read, browser_go and the
web parts of list_elements / click / type_text). page_control is always faked."""
import unittest
from unittest import mock

from desktop_ai_agent.action import handlers
from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.action.registry import build_default_registry
from desktop_ai_agent.action.tools import page_control, ui_control
from desktop_ai_agent.input.ui_elements import normalize_address, pick_address


def tool(name):
    return next(t["function"] for t in build_default_registry().to_tools() if t["function"]["name"] == name)


class TestRegistry(unittest.TestCase):
    def test_new_tools_and_risk(self):
        reg = build_default_registry()
        self.assertEqual(reg.get("browser_read").risk.value, "low")
        self.assertEqual(reg.get("browser_go").risk.value, "medium")
        self.assertEqual(tool("browser_go")["parameters"]["properties"]["action"]["enum"], ["back", "forward", "reload"])

    def test_injected_values_are_hidden_from_the_model(self):
        for name in ("list_elements", "browser_read", "click"):
            with self.subTest(name=name):
                visible = tool(name)["parameters"]["properties"]
                self.assertNotIn("request", visible)
                self.assertNotIn("cloud", visible)
        self.assertIn("request", [p.name for p in build_default_registry().get("click").params])

    def test_page_area_and_submit(self):
        self.assertIn("page", tool("list_elements")["parameters"]["properties"]["area"]["enum"])
        self.assertEqual(tool("type_text")["parameters"]["properties"]["submit"]["type"], "boolean")
        self.assertNotIn("submit", tool("type_text")["parameters"]["required"])

    def test_bad_arguments_rejected(self):
        ex = default_executor()
        for step in ({"name": "browser_go", "arguments": {"action": "home"}},
                     {"name": "browser_read", "arguments": {"part": 0}},
                     {"name": "browser_read", "arguments": {"find": "x" * 101}},
                     {"name": "type_text", "arguments": {"element": 1, "text": "a", "submit": "yes"}}):
            with self.subTest(step=step):
                (r,) = ex.run([step])
                self.assertEqual(r.status, "rejected")


class TestHandlers(unittest.TestCase):
    def test_browser_read_passes_everything_and_hands_over(self):
        with mock.patch.object(ui_control, "user_browser_in_front", return_value=600), \
                mock.patch.object(page_control, "read", return_value={"text": "hi"}) as read:
            out = handlers.browser_read({"find": "delete", "request": "find delete", "cloud": True})
        read.assert_called_once_with("delete", 1, "find delete", True, 600)
        self.assertEqual(out, {"text": "hi"})

    def test_browser_go_clears_the_element_list(self):
        with mock.patch.object(page_control, "go", return_value={"action": "back"}) as go, \
                mock.patch.object(ui_control, "clear_list_after_action") as clear:
            handlers.browser_go({"action": "back"})
        go.assert_called_once_with("back")
        clear.assert_called_once_with("browser_go")

    def test_click_and_type_pass_request_and_submit(self):
        with mock.patch.object(ui_control, "click", return_value={}) as click, \
                mock.patch.object(ui_control, "type_text", return_value={}) as type_text:
            handlers.click({"element": 2, "request": "yes accept"})
            handlers.type_text({"element": 1, "text": "lofi", "submit": True})
        click.assert_called_once_with(2, "left", False, "yes accept")
        type_text.assert_called_once_with(1, "lofi", True)


class TestAddressBar(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_address("youtube.com/watch?v=x"), "https://youtube.com/watch?v=x")
        self.assertEqual(normalize_address("http://x.org"), "http://x.org")
        self.assertEqual(normalize_address("localhost:8000/a"), "https://localhost:8000/a")
        for value in ("opera://startpage", "about:blank", "file:///C:/a", "lofi music", "x", "", None):
            with self.subTest(value=value):
                self.assertIsNone(normalize_address(value))

    def test_pick_the_first_web_address(self):
        self.assertEqual(pick_address(["", "lofi", "www.youtube.com/results"]), "https://www.youtube.com/results")
        self.assertIsNone(pick_address(["Search", ""]))


if __name__ == "__main__":
    unittest.main()
