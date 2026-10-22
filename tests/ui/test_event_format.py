import unittest

from desktop_ai_agent.action.executor import StepResult
from desktop_ai_agent.ui.event_format import format_event


class TestFormatEvent(unittest.TestCase):
    def test_plan_line(self):
        plan = [{"name": "wait", "arguments": {"seconds": 2}}, {"name": "screenshot", "arguments": {}}]
        self.assertEqual(format_event("plan", plan), "[plan] wait(seconds=2) -> screenshot()\n")

    def test_plan_with_garbage_arguments_does_not_crash(self):
        self.assertIn("click(", format_event("plan", [{"name": "click", "arguments": "x=1"}]))
        self.assertIn("5", format_event("plan", [5]))

    def test_ok_result(self):
        r = StepResult(0, "wait", "executed", output={"waited_seconds": 2})
        self.assertEqual(format_event("result", r), "[ok] wait -> {'waited_seconds': 2}\n")

    def test_failure_results_show_status_and_reason(self):
        r = StepResult(1, "click", "rejected", error="'x' must be <= 3840")
        self.assertEqual(format_event("result", r), "[rejected] click: 'x' must be <= 3840\n")

    def test_long_output_is_clipped(self):
        r = StepResult(0, "read", "executed", output="x" * 1000)
        self.assertLess(len(format_event("result", r)), 260)

    def test_final_and_unknown(self):
        self.assertEqual(format_event("final", "All done"), "All done\n")
        self.assertEqual(format_event("mystery", None), "")

    def test_auto_screenshot_line(self):
        self.assertEqual(format_event("screenshot", "auto"), "[screenshot] auto\n")


if __name__ == "__main__":
    unittest.main()