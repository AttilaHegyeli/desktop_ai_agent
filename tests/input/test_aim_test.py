"""Tests for scripts/aim_test.py with a fake model and a fake screenshot.
No real screen is captured and no Ollama is needed."""
import base64
import importlib.util
import inspect
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


def load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "aim_test.py"
    spec = importlib.util.spec_from_file_location("aim_test_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["aim_test_script"] = module  # dataclasses needs the module to be registered
    spec.loader.exec_module(module)
    return module


aim = load_script()


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.png = self.dir / "screen.png"
        Image.new("RGB", (800, 600), "white").save(self.png)
        self.calls = []  # (prompt, base64) for every question asked

    def capture(self):
        return {"path": str(self.png), "width": 800, "height": 600, "left": -1920, "top": 0, "captured_at": 0}

    def run_test(self, ask, targets=("the Start button",), **kwargs):
        def recording_ask(prompt, b64):
            self.calls.append((prompt, b64))
            return ask(prompt, b64)

        return aim.run_aim_test(
            list(targets), capture=self.capture, ask=recording_ask,
            out_dir=self.dir / "out", log=lambda _m: None, **kwargs,
        )


def friendly_model(prompt, _b64):
    if "zoomed crop" in prompt:
        return '{"x": 500, "y": 500}'
    if "(1000,1000)" in prompt:
        return '{"x": 250, "y": 500}'
    return '{"x": 200, "y": 300}'


class TestFullFlow(Case):
    def test_all_three_methods_and_screen_mapping(self):
        (row,) = self.run_test(friendly_model)
        shots = row["shots"]
        self.assertEqual(shots["P"]["image_px"], (200, 300))
        self.assertEqual(shots["G"]["image_px"], (200, 300))  # 250/1000*800, 500/1000*600
        self.assertEqual(shots["R"]["image_px"], (200, 300))  # crop (0,100,400,400), centre of it
        self.assertEqual(shots["P"]["screen_px"], (-1720, 300))  # -1920 + 200
        self.assertEqual(len(self.calls), 3)

    def test_files_are_written(self):
        (row,) = self.run_test(friendly_model)
        out = self.dir / "out"
        self.assertTrue((out / row["image"]).is_file())
        self.assertTrue((out / row["crop"]).is_file())
        self.assertTrue((out / "results.json").is_file())

    def test_the_refine_crop_is_zoomed(self):
        self.run_test(friendly_model, crop_size=400, zoom=2)
        refine_b64 = next(b for p, b in self.calls if "zoomed crop" in p)
        crop = Image.open(io.BytesIO(base64.b64decode(refine_b64)))
        self.assertEqual(crop.size, (800, 800))  # 400 px crop x zoom 2

    def test_results_json_is_readable(self):
        self.run_test(friendly_model)
        data = json.loads((self.dir / "out" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(data["capture"], {"width": 800, "height": 600, "left": -1920, "top": 0})
        self.assertEqual(data["results"][0]["shots"]["P"]["image_px"], [200, 300])

    def test_markers_are_drawn_on_the_saved_image(self):
        (row,) = self.run_test(friendly_model)
        marked = Image.open(self.dir / "out" / row["image"]).convert("RGB")
        self.assertNotEqual(marked.getpixel((200, 300)), (255, 255, 255))
        self.assertEqual(marked.getpixel((700, 550)), (255, 255, 255))  # far away: untouched

    def test_several_targets(self):
        rows = self.run_test(friendly_model, targets=["a", "b", "c"])
        self.assertEqual([r["n"] for r in rows], [1, 2, 3])
        self.assertEqual(len(self.calls), 9)


class TestEdgeCases(Case):
    def test_no_refine_asks_two_questions_per_target(self):
        (row,) = self.run_test(friendly_model, refine=False)
        self.assertNotIn("R", row["shots"])
        self.assertIsNone(row["crop"])
        self.assertEqual(len(self.calls), 2)

    def test_grid_answer_outside_the_image_skips_refine(self):
        def model(prompt, _b64):
            return '{"x": 5000, "y": 5000}' if "(1000,1000)" in prompt else '{"x": 200, "y": 300}'

        (row,) = self.run_test(model)
        self.assertEqual(row["shots"]["G"]["note"], "outside image")
        self.assertNotIn("R", row["shots"])
        self.assertEqual(len(self.calls), 2)

    def test_reply_without_a_point_is_recorded(self):
        (row,) = self.run_test(lambda p, b: "I cannot find it on this screen.")
        self.assertEqual(row["shots"]["P"]["note"], "no point in reply")
        self.assertIsNone(row["shots"]["P"]["image_px"])
        self.assertNotIn("R", row["shots"])

    def test_model_errors_are_recorded_and_the_run_continues(self):
        def boom(prompt, _b64):
            raise RuntimeError("ollama down")

        rows = self.run_test(boom, targets=["a", "b"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["shots"]["P"]["note"].startswith("error: ollama down"))
        self.assertTrue((self.dir / "out" / rows[1]["image"]).is_file())

    def test_a_point_on_the_right_edge_is_pulled_inside(self):
        (row,) = self.run_test(lambda p, b: '{"x": 1000, "y": 1000}' if "(1000,1000)" in p else '{"x": 1, "y": 1}')
        self.assertEqual(row["shots"]["G"]["image_px"], (799, 599))


class TestCommandLineAndModelCall(unittest.TestCase):
    def test_defaults_and_flags(self):
        args = aim.parse_args([])
        self.assertEqual((args.model, args.crop, args.zoom, args.quick, args.no_refine), ("gemma4", 400, 2, False, False))
        args = aim.parse_args(["--quick", "--crop", "300", "--no-refine", "--model", "x"])
        self.assertEqual((args.quick, args.crop, args.no_refine, args.model), (True, 300, True, "x"))

    def test_the_default_targets_file_has_targets(self):
        from desktop_ai_agent.input.geometry import parse_targets

        text = Path(aim.parse_args([]).targets).read_text(encoding="utf-8")
        self.assertGreaterEqual(len(parse_targets(text)), 1)

    def test_make_ask_builds_the_client_as_intended(self):
        made = {}

        class FakeClient:
            def __init__(self, model, host, **kwargs):
                made.update(model=model, host=host, **kwargs)
                self.sent = []

            def add_user(self, text, images=None):
                made["prompt"], made["images"] = text, images

            def chat(self):
                return mock.Mock(content='{"x": 1, "y": 2}')

        with mock.patch("desktop_ai_agent.core.chat_client.ChatClient", FakeClient):
            reply = aim.make_ask("gemma4", "http://h:1", 4096)("where?", "B64")
        self.assertEqual(reply, '{"x": 1, "y": 2}')
        self.assertEqual(made["images"], ["B64"])
        self.assertEqual(made["response_format"], aim.POINT_SCHEMA)
        self.assertEqual((made["system"], made["normalize"], made["think"]), ("", False, False))
        self.assertEqual(made["options"], {"temperature": 0, "num_ctx": 4096})

    def test_every_keyword_make_ask_uses_exists_on_the_real_chatclient(self):
        from desktop_ai_agent.core.chat_client import ChatClient

        params = inspect.signature(ChatClient.__init__).parameters
        for name in ("think", "response_format", "system", "normalize", "options"):
            self.assertIn(name, params)


if __name__ == "__main__":
    unittest.main()