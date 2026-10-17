import unittest

from desktop_ai_agent.action.registry import (
    ActionRegistry,
    ActionSpec,
    Param,
    Risk,
    ValidationError,
    build_default_registry,
)


def click_spec():
    return ActionSpec(
        "click",
        "Left-click at screen coordinates",
        Risk.MEDIUM,
        (
            Param("x", "integer", "x", minimum=0, maximum=3840),
            Param("y", "integer", "y", minimum=0, maximum=2160),
        ),
    )


class TestValidation(unittest.TestCase):
    def test_valid_args_pass(self):
        self.assertEqual(click_spec().validate({"x": 10, "y": 20}), {"x": 10, "y": 20})

    def test_missing_required(self):
        with self.assertRaisesRegex(ValidationError, "missing required parameter 'y'"):
            click_spec().validate({"x": 10})

    def test_unknown_parameter_rejected(self):
        with self.assertRaisesRegex(ValidationError, "unknown parameter"):
            click_spec().validate({"x": 1, "y": 2, "button": "right"})

    def test_wrong_type(self):
        with self.assertRaisesRegex(ValidationError, "must be integer"):
            click_spec().validate({"x": "10", "y": 2})

    def test_bool_not_accepted_as_number(self):
        with self.assertRaisesRegex(ValidationError, "got boolean"):
            click_spec().validate({"x": True, "y": 2})

    def test_out_of_range(self):
        with self.assertRaisesRegex(ValidationError, "<= 3840"):
            click_spec().validate({"x": 99999, "y": 2})

    def test_non_object_arguments(self):
        with self.assertRaisesRegex(ValidationError, "must be an object"):
            click_spec().validate("x=1")

    def test_enum_and_max_length(self):
        spec = ActionSpec(
            "key", "press", Risk.MEDIUM,
            (Param("mode", "string", "m", enum=("tap", "hold")),
             Param("text", "string", "t", required=False, max_length=3)),
        )
        with self.assertRaisesRegex(ValidationError, "one of"):
            spec.validate({"mode": "smash"})
        with self.assertRaisesRegex(ValidationError, "too long"):
            spec.validate({"mode": "tap", "text": "abcd"})
        self.assertEqual(spec.validate({"mode": "tap"}), {"mode": "tap"})


class TestRegistry(unittest.TestCase):
    def test_unknown_action_message_lists_available(self):
        reg = build_default_registry()
        with self.assertRaisesRegex(ValidationError, "Unknown action 'press_button'.*screenshot"):
            reg.get("press_button")

    def test_non_string_name_rejected(self):
        with self.assertRaises(ValidationError):
            build_default_registry().get(None)

    def test_duplicate_registration_fails(self):
        reg = ActionRegistry([click_spec()])
        with self.assertRaises(ValueError):
            reg.register(click_spec())

    def test_tools_schema_shape(self):
        tools = ActionRegistry([click_spec()]).to_tools()
        fn = tools[0]["function"]
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(fn["name"], "click")
        self.assertEqual(fn["parameters"]["required"], ["x", "y"])
        self.assertEqual(fn["parameters"]["properties"]["x"]["maximum"], 3840)
        self.assertFalse(fn["parameters"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
