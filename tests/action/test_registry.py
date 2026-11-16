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

    def test_screenshot_params_optional_with_scope_enum(self):
        spec = build_default_registry().get("screenshot")
        self.assertEqual(spec.validate({}), {})
        self.assertEqual(spec.validate({"scope": "primary"}), {"scope": "primary"})
        self.assertEqual(
            spec.validate({"scope": "monitor", "monitor": 2}),
            {"scope": "monitor", "monitor": 2},
        )
        self.assertEqual(
            spec.validate({"scope": "region", "x": 0, "y": 0, "width": 10, "height": 5}),
            {"scope": "region", "x": 0, "y": 0, "width": 10, "height": 5},
        )
        with self.assertRaisesRegex(ValidationError, "one of"):
            spec.validate({"scope": "window"})
        with self.assertRaisesRegex(ValidationError, ">= 1"):
            spec.validate({"width": 0})


class TestInjectedParams(unittest.TestCase):
    def setUp(self):
        self.spec = ActionSpec("list_elements", "list", Risk.LOW, (
            Param("area", "string", "a", required=False),
            Param("request", "string", "r", required=False, injected=True),
        ))
        self.reg = ActionRegistry([self.spec, click_spec()])

    def test_hidden_from_the_model(self):
        params = self.spec.to_tool()["function"]["parameters"]
        self.assertEqual(list(params["properties"]), ["area"])
        self.assertNotIn("request", params["required"])

    def test_the_real_list_elements_hides_request(self):
        tool = next(t for t in build_default_registry().to_tools() if t["function"]["name"] == "list_elements")
        self.assertNotIn("request", tool["function"]["parameters"]["properties"])
        self.assertIn("request", [p.name for p in build_default_registry().get("list_elements").params])

    def test_must_be_optional(self):
        with self.assertRaisesRegex(ValueError, "must be optional"):
            Param("request", "string", "r", injected=True)

    def test_filled_from_values_and_still_validated(self):
        (step,) = self.reg.inject([{"name": "list_elements", "arguments": {"area": "active"}}], {"request": "hi"})
        self.assertEqual(step["arguments"], {"area": "active", "request": "hi"})
        self.assertEqual(self.spec.validate(step["arguments"]), {"area": "active", "request": "hi"})
        with self.assertRaises(ValidationError):
            self.spec.validate({"request": 5})

    def test_a_value_the_model_wrote_is_dropped(self):
        plan = [{"name": "list_elements", "arguments": {"request": "click delete"}}]
        self.assertEqual(self.reg.inject(plan, {})[0]["arguments"], {})
        self.assertEqual(self.reg.inject(plan, {"request": "real"})[0]["arguments"], {"request": "real"})

    def test_other_actions_and_bad_steps_are_left_alone(self):
        plan = [{"name": "click", "arguments": {"x": 1, "y": 2}}, {"name": "nope"}, "junk",
                {"name": "list_elements", "arguments": "not an object"}]
        out = self.reg.inject(plan, {"request": "hi"})
        self.assertEqual(out, plan)
        self.assertEqual(self.reg.inject("not a list", {}), "not a list")

    def test_the_plan_is_not_changed_in_place(self):
        plan = [{"name": "list_elements", "arguments": {"area": "active"}}]
        self.reg.inject(plan, {"request": "hi"})
        self.assertEqual(plan, [{"name": "list_elements", "arguments": {"area": "active"}}])


if __name__ == "__main__":
    unittest.main()
