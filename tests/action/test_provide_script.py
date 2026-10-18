import unittest

from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.action.registry import Risk, ValidationError, build_default_registry


class TestProvideScript(unittest.TestCase):
    def spec(self):
        return build_default_registry().get("provide_script")

    def test_is_low_risk_and_ends_the_turn(self):
        self.assertEqual(self.spec().risk, Risk.LOW)
        self.assertTrue(self.spec().ends_turn)

    def test_other_actions_do_not_end_the_turn(self):
        reg = build_default_registry()
        self.assertFalse(reg.get("wait").ends_turn)
        self.assertFalse(reg.get("screenshot").ends_turn)

    def test_valid_arguments_pass(self):
        args = {"script": "print hello", "code": "print('hello')\n"}
        self.assertEqual(self.spec().validate(args), args)

    def test_missing_fields_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "missing required parameter 'code'"):
            self.spec().validate({"script": "x"})
        with self.assertRaisesRegex(ValidationError, "missing required parameter 'script'"):
            self.spec().validate({"code": "x"})

    def test_oversized_fields_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "too long"):
            self.spec().validate({"script": "x" * 201, "code": "x"})
        with self.assertRaisesRegex(ValidationError, "too long"):
            self.spec().validate({"script": "x", "code": "x" * 20001})

    def test_wrong_types_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "must be string"):
            self.spec().validate({"script": "x", "code": 123})

    def test_executor_returns_the_data_and_never_runs_the_code(self):
        args = {"script": "dangerous", "code": "raise RuntimeError('this must not run')"}
        res = default_executor().run([{"name": "provide_script", "arguments": args}])
        self.assertTrue(res[0].ok)
        self.assertEqual(res[0].output, args)

    def test_model_sees_it_as_a_tool_with_both_fields_required(self):
        tools = {t["function"]["name"]: t["function"] for t in build_default_registry().to_tools()}
        params = tools["provide_script"]["parameters"]
        self.assertEqual(sorted(params["required"]), ["code", "script"])
        self.assertEqual(params["properties"]["script"]["maxLength"], 200)


if __name__ == "__main__":
    unittest.main()