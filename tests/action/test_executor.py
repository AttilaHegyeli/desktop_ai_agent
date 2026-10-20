import json
import unittest
from types import SimpleNamespace

from desktop_ai_agent.action.audit_log import AuditLog
from desktop_ai_agent.action.executor import Executor, plan_from_tool_calls, default_executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk


def build(confirm=None, max_steps=10, fail_on=None):
    reg = ActionRegistry([
        ActionSpec("peek", "look", Risk.LOW),
        ActionSpec("click", "click", Risk.MEDIUM, (
            Param("x", "integer", "x", minimum=0, maximum=3840),
            Param("y", "integer", "y", minimum=0, maximum=2160),
        )),
        ActionSpec("delete_file", "delete", Risk.HIGH, (Param("path", "string", "p", max_length=260),)),
    ])
    calls = []

    def make(name):
        def fn(args):
            if fail_on == name:
                raise RuntimeError("boom")
            calls.append((name, args))
            return "ok"
        return fn

    handlers = {n: make(n) for n in ("peek", "click", "delete_file")}
    audit = AuditLog()
    return Executor(reg, handlers, confirm=confirm, audit=audit, max_steps=max_steps), calls, audit


class TestGate(unittest.TestCase):
    def test_valid_plan_executes_in_order(self):
        ex, calls, _ = build()
        res = ex.run([{"name": "peek"}, {"name": "click", "arguments": {"x": 5, "y": 6}}])
        self.assertTrue(all(r.ok for r in res))
        self.assertEqual([c[0] for c in calls], ["peek", "click"])

    def test_unknown_action_rejected_and_handler_not_called(self):
        ex, calls, _ = build()
        res = ex.run([{"name": "format_disk"}])
        self.assertEqual(res[0].status, "rejected")
        self.assertIn("Unknown action", res[0].error)
        self.assertEqual(calls, [])

    def test_bad_parameters_rejected(self):
        ex, calls, _ = build()
        res = ex.run([{"name": "click", "arguments": {"x": 99999, "y": 1}}])
        self.assertEqual(res[0].status, "rejected")
        self.assertEqual(calls, [])

    def test_bad_step_means_nothing_runs(self):
        ex, calls, _ = build()
        res = ex.run([{"name": "peek"}, {"name": "click", "arguments": {"x": "a", "y": 1}}])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].status, "rejected")
        self.assertEqual(res[0].index, 1)
        self.assertEqual(calls, [], "valid first step must not run when a later step is invalid")

    def test_high_risk_without_confirm_callback_is_declined(self):
        ex, calls, _ = build(confirm=None)
        res = ex.run([{"name": "delete_file", "arguments": {"path": "a.txt"}}])
        self.assertEqual(res[0].status, "declined")
        self.assertEqual(calls, [])

    def test_high_risk_user_says_no(self):
        ex, calls, _ = build(confirm=lambda spec, args: False)
        res = ex.run([{"name": "delete_file", "arguments": {"path": "a.txt"}}])
        self.assertEqual(res[0].status, "declined")
        self.assertEqual(calls, [])

    def test_high_risk_user_says_yes(self):
        seen = []
        ex, calls, _ = build(confirm=lambda spec, args: seen.append((spec.name, args)) or True)
        res = ex.run([{"name": "delete_file", "arguments": {"path": "a.txt"}}])
        self.assertTrue(res[0].ok)
        self.assertEqual(seen, [("delete_file", {"path": "a.txt"})])
        self.assertEqual(calls, [("delete_file", {"path": "a.txt"})])

    def test_confirm_callback_crash_means_decline(self):
        def bad(spec, args):
            raise RuntimeError("ui died")
        ex, calls, _ = build(confirm=bad)
        res = ex.run([{"name": "delete_file", "arguments": {"path": "a"}}])
        self.assertEqual(res[0].status, "declined")

    def test_decline_stops_the_rest_of_the_plan(self):
        ex, calls, _ = build(confirm=lambda s, a: False)
        res = ex.run([{"name": "peek"}, {"name": "delete_file", "arguments": {"path": "a"}}, {"name": "peek"}])
        self.assertEqual([r.status for r in res], ["executed", "declined"])
        self.assertEqual(len(calls), 1)

    def test_handler_exception_becomes_error_and_stops(self):
        ex, calls, _ = build(fail_on="click")
        res = ex.run([{"name": "click", "arguments": {"x": 1, "y": 1}}, {"name": "peek"}])
        self.assertEqual(res[0].status, "error")
        self.assertIn("boom", res[0].error)
        self.assertEqual(len(res), 1)

    def test_max_steps_enforced(self):
        ex, calls, _ = build(max_steps=2)
        res = ex.run([{"name": "peek"}] * 3)
        self.assertEqual(res[0].status, "rejected")
        self.assertEqual(calls, [])

    def test_malformed_plans(self):
        ex, calls, _ = build()
        for bad in (None, [], "peek", [{"arguments": {}}], ["peek"], [{"name": "peek", "arguments": "x"}]):
            self.assertEqual(ex.run(bad)[0].status, "rejected", msg=repr(bad))
        self.assertEqual(calls, [])

    def test_registry_handler_mismatch_fails_at_startup(self):
        reg = ActionRegistry([ActionSpec("peek", "look", Risk.LOW)])
        with self.assertRaisesRegex(ValueError, "mismatch"):
            Executor(reg, {})
        with self.assertRaisesRegex(ValueError, "mismatch"):
            Executor(reg, {"peek": lambda a: 1, "extra": lambda a: 1})


class TestAuditAndMessages(unittest.TestCase):
    def test_every_outcome_is_logged(self):
        ex, _, audit = build()
        ex.run([{"name": "nope"}])
        ex.run([{"name": "delete_file", "arguments": {"path": "a"}}])
        ex.run([{"name": "peek"}])
        events = [e["event"] for e in audit.entries]
        self.assertEqual(events, ["rejected", "declined", "execute", "result"])

    def test_audit_writes_jsonl_file(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "logs" / "audit.jsonl"
            log = AuditLog(path)
            log.record("execute", action="peek", args={"x": 1})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0])["action"], "peek")

    def test_long_fields_are_truncated(self):
        log = AuditLog(max_field_chars=20)
        e = log.record("result", output="x" * 500)
        self.assertLess(len(e["output"]), 60)

    def test_error_message_for_model_is_json_with_reason(self):
        ex, _, _ = build()
        msg = json.loads(ex.run([{"name": "nope"}])[0].to_message())
        self.assertEqual(msg["status"], "rejected")
        self.assertIn("Available", msg["error"])


class TestPlanParsing(unittest.TestCase):
    def test_ollama_style_objects(self):
        call = SimpleNamespace(function=SimpleNamespace(name="click", arguments={"x": 1, "y": 2}))
        self.assertEqual(plan_from_tool_calls([call]), [{"name": "click", "arguments": {"x": 1, "y": 2}}])

    def test_openai_style_json_string_arguments(self):
        call = {"function": {"name": "click", "arguments": '{"x": 1, "y": 2}'}}
        self.assertEqual(plan_from_tool_calls([call])[0]["arguments"], {"x": 1, "y": 2})

    def test_broken_json_is_left_for_validator_to_reject(self):
        ex, calls, _ = build()
        plan = plan_from_tool_calls([{"function": {"name": "click", "arguments": "{x: 1"}}])
        self.assertEqual(ex.run(plan)[0].status, "rejected")
        self.assertEqual(calls, [])

    def test_no_tool_calls_gives_empty_plan(self):
        self.assertEqual(plan_from_tool_calls(None), [])


class TestDefaultSetup(unittest.TestCase):
    def test_default_executor_builds_and_wait_runs(self):
        ex = default_executor()
        res = ex.run([{"name": "wait", "arguments": {"seconds": 0}}])
        self.assertTrue(res[0].ok)

    def test_default_screenshot_reports_missing_deps_instead_of_crashing(self):
        from unittest.mock import patch

        with patch(
            "desktop_ai_agent.action.handlers.capture_screen",
            side_effect=ImportError('screenshot requires mss and Pillow. Install with: pip install -e ".[screenshot]"'),
        ):
            res = default_executor().run([{"name": "screenshot"}])
        self.assertEqual(res[0].status, "error")
        self.assertIn("mss", res[0].error)

    def test_default_screenshot_returns_path_metadata(self):
        from unittest.mock import patch

        fake = {"path": "C:/tmp/shot.png", "width": 10, "height": 20}
        with patch("desktop_ai_agent.action.handlers.capture_screen", return_value=fake):
            res = default_executor().run([{"name": "screenshot"}])
        self.assertTrue(res[0].ok)
        self.assertEqual(res[0].output, fake)

    def test_wait_limit_enforced(self):
        res = default_executor().run([{"name": "wait", "arguments": {"seconds": 999}}])
        self.assertEqual(res[0].status, "rejected")


if __name__ == "__main__":
    unittest.main()
