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


def image_size(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64))).size


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


def native_model(_prompt, b64):
    """Answers box_2d (y first). The full 800x600 shot and the 800x800 crop get different boxes."""
    if image_size(b64) == (800, 800):  # the zoomed crop
        return '[{"box_2d": [400, 400, 600, 600], "label": "start"}]'
    return '```json\n[{"box_2d": [450, 200, 550, 300], "label": "start"}]\n```'


def legacy_model(prompt, _b64):
    if "zoomed crop" in prompt:
        return '{"x": 500, "y": 500}'
    if "(1000,1000)" in prompt:
        return '{"x": 250, "y": 500}'
    return '{"x": 200, "y": 300}'


class TestNativeFlow(Case):
    def test_native_and_refine_with_screen_mapping(self):
        (row,) = self.run_test(native_model)
        shots = row["shots"]
        self.assertEqual(set(shots), {"N", "R"})
        # N: box x 200..300, y 450..550 -> centre (250, 500) -> 800x600 image -> (200, 300)
        self.assertEqual(shots["N"]["box"], (200.0, 450.0, 300.0, 550.0))
        self.assertEqual(shots["N"]["center"], (250.0, 500.0))
        self.assertEqual(shots["N"]["image_px"], (200, 300))
        self.assertEqual(shots["N"]["box_px"], (160, 270, 240, 330))
        self.assertEqual(shots["N"]["screen_px"], (-1720, 300))  # -1920 + 200
        # R: crop (0,100)-(400,500); centre of the crop grid -> (200, 300)
        self.assertEqual(shots["R"]["image_px"], (200, 300))
        self.assertEqual(shots["R"]["box_px"], (160, 260, 240, 340))
        self.assertEqual(len(self.calls), 2)

    def test_refine_uses_the_same_detect_prompt(self):
        self.run_test(native_model, targets=["the clock"])
        prompts = [p for p, _ in self.calls]
        self.assertEqual(prompts[0], prompts[1])
        self.assertTrue(prompts[0].startswith("Detect: the clock\n"))
        self.assertIn('"box_2d": [y1, x1, y2, x2]', prompts[0])

    def test_the_refine_crop_is_zoomed(self):
        self.run_test(native_model, crop_size=400, zoom=2)
        self.assertEqual(image_size(self.calls[1][1]), (800, 800))  # 400 px crop x zoom 2

    def test_files_are_written(self):
        (row,) = self.run_test(native_model)
        out = self.dir / "out"
        self.assertTrue((out / row["image"]).is_file())
        self.assertTrue((out / row["crop"]).is_file())
        self.assertTrue((out / "results.json").is_file())

    def test_results_json_records_reply_box_centre_and_pixels(self):
        self.run_test(native_model)
        data = json.loads((self.dir / "out" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(data["capture"], {"width": 800, "height": 600, "left": -1920, "top": 0})
        self.assertEqual(data["mode"], "native")
        n = data["results"][0]["shots"]["N"]
        self.assertIn("box_2d", n["raw"])
        self.assertEqual(n["box"], [200.0, 450.0, 300.0, 550.0])
        self.assertEqual(n["center"], [250.0, 500.0])
        self.assertEqual(n["image_px"], [200, 300])
        self.assertEqual(n["screen_px"], [-1720, 300])

    def test_box_and_cross_are_drawn_on_the_saved_image(self):
        (row,) = self.run_test(native_model, refine=False)
        marked = Image.open(self.dir / "out" / row["image"]).convert("RGB")
        self.assertNotEqual(marked.getpixel((200, 300)), (255, 255, 255))  # cross
        self.assertNotEqual(marked.getpixel((160, 285)), (255, 255, 255))  # left edge of the box
        self.assertEqual(marked.getpixel((700, 550)), (255, 255, 255))  # far away: untouched

    def test_several_targets(self):
        rows = self.run_test(native_model, targets=["a", "b", "c"])
        self.assertEqual([r["n"] for r in rows], [1, 2, 3])
        self.assertEqual(len(self.calls), 6)


class TestEdgeCases(Case):
    def test_no_refine_asks_one_question_per_target(self):
        (row,) = self.run_test(native_model, refine=False)
        self.assertEqual(set(row["shots"]), {"N"})
        self.assertIsNone(row["crop"])
        self.assertEqual(len(self.calls), 1)

    def test_empty_list_means_not_found_and_skips_refine(self):
        (row,) = self.run_test(lambda p, b: "[]")
        self.assertEqual(row["shots"]["N"]["note"], "not found")
        self.assertIsNone(row["shots"]["N"]["image_px"])
        self.assertNotIn("R", row["shots"])
        self.assertEqual(len(self.calls), 1)

    def test_reply_without_a_box_is_recorded(self):
        (row,) = self.run_test(lambda p, b: "Sorry, no idea.")
        self.assertEqual(row["shots"]["N"]["note"], "no box in reply")

    def test_prompt_allows_not_found(self):
        self.run_test(native_model, refine=False)
        self.assertIn("If it is not visible in this image, reply [].", self.calls[0][0])

    def test_a_box_far_outside_the_grid_is_rejected(self):
        (row,) = self.run_test(lambda p, b: '{"box_2d": [0, 0, 5000, 5000]}')
        self.assertEqual(row["shots"]["N"]["note"], "no box in reply")

    def test_model_errors_are_recorded_and_the_run_continues(self):
        def boom(prompt, _b64):
            raise RuntimeError("ollama down")

        rows = self.run_test(boom, targets=["a", "b"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["shots"]["N"]["note"].startswith("error: ollama down"))
        self.assertTrue((self.dir / "out" / rows[1]["image"]).is_file())

    def test_a_box_at_the_bottom_right_edge_is_pulled_inside(self):
        (row,) = self.run_test(lambda p, b: '{"box_2d": [1000, 1000, 1000, 1000]}', refine=False)
        self.assertEqual(row["shots"]["N"]["image_px"], (799, 599))


RED = (255, 0, 0)


def seeing_model(_prompt, b64):
    """A perfect fake model: finds the red square in the image it was sent and answers
    its true box_2d (y first, 0..1000 of that image, padding included)."""
    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    mask = Image.eval(image.split()[0], lambda v: 255 if v > 200 else 0)
    green = Image.eval(image.split()[1], lambda v: 255 if v < 50 else 0)
    from PIL import ImageChops

    bbox = ImageChops.multiply(mask, green).getbbox()
    if bbox is None:
        return "[]"
    w, h = image.size
    x1, y1, x2, y2 = bbox
    return json.dumps([{"box_2d": [y1 / h * 1000, x1 / w * 1000, y2 / h * 1000, x2 / w * 1000], "label": "red"}])


class TestTilesAndPadding(Case):
    def setUp(self):
        super().setUp()
        image = Image.new("RGB", (800, 600), "white")
        image.paste(RED, (600, 450, 640, 490))  # centre (620, 470)
        image.save(self.png)

    def test_whole_screen_without_options_finds_the_square(self):
        (row,) = self.run_test(seeing_model)
        self.assertEqual(row["shots"]["N"]["image_px"], (620, 470))
        self.assertEqual(row["shots"]["R"]["image_px"], (620, 470))

    def test_pad_sends_a_square_and_maps_back_correctly(self):
        (row,) = self.run_test(seeing_model, pad=True)
        self.assertEqual(image_size(self.calls[0][1]), (800, 800))  # 800x600 padded
        self.assertEqual(row["shots"]["N"]["image_px"], (620, 470))
        self.assertEqual(row["shots"]["N"]["box_px"], (600, 450, 640, 490))
        self.assertEqual(row["shots"]["R"]["image_px"], (620, 470))

    def test_an_answer_in_the_padding_is_outside_the_image(self):
        (row,) = self.run_test(lambda p, b: '{"box_2d": [900, 100, 950, 200]}', pad=True, refine=False)
        self.assertEqual(row["shots"]["N"]["note"], "outside image")  # y = 0.925 * 800 = 740 > 600

    def test_tiles_ask_every_tile_and_keep_the_one_that_found_it(self):
        (row,) = self.run_test(seeing_model, tiles=2, refine=False)
        n = row["shots"]["N"]
        self.assertEqual(len(self.calls), 4)  # 2 x 2 tiles
        self.assertEqual(image_size(self.calls[0][1]), (500, 375))  # 400x300 grown by 25 %
        self.assertEqual(n["image_px"], (620, 470))
        self.assertEqual([t["note"] for t in n["tiles"]], ["not found", "not found", "not found", ""])
        self.assertEqual([t["chosen"] for t in n["tiles"]], [False, False, False, True])

    def test_tiles_with_pad_then_refine(self):
        (row,) = self.run_test(seeing_model, tiles=2, pad=True)
        self.assertEqual(image_size(self.calls[0][1]), (500, 500))
        self.assertEqual(row["shots"]["N"]["image_px"], (620, 470))
        self.assertEqual(row["shots"]["R"]["image_px"], (620, 470))
        self.assertEqual(len(self.calls), 5)

    def test_no_tile_finds_it(self):
        (row,) = self.run_test(lambda p, b: "[]", tiles=2)
        self.assertEqual(row["shots"]["N"]["note"], "not found")
        self.assertNotIn("R", row["shots"])

    def test_all_tiles_failing_reports_the_error(self):
        def boom(prompt, _b64):
            raise RuntimeError("ollama down")

        (row,) = self.run_test(boom, tiles=2)
        self.assertTrue(row["shots"]["N"]["note"].startswith("error: ollama down"))

    def test_losing_tile_answers_are_drawn_and_saved(self):
        (row,) = self.run_test(lambda p, b: '{"box_2d": [100, 100, 200, 200]}', tiles=2, refine=False)
        n = row["shots"]["N"]
        self.assertEqual(sum(t["chosen"] for t in n["tiles"]), 1)
        data = json.loads((self.dir / "out" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual((data["tiles"], data["pad"]), (2, False))
        self.assertEqual(len(data["results"][0]["shots"]["N"]["tiles"]), 4)


class TestLegacy(Case):
    def test_legacy_asks_pixels_grid_and_point_refine(self):
        (row,) = self.run_test(legacy_model, legacy=True)
        shots = row["shots"]
        self.assertEqual(set(shots), {"P", "G", "R"})
        self.assertEqual(shots["P"]["image_px"], (200, 300))
        self.assertEqual(shots["G"]["image_px"], (200, 300))  # 250/1000*800, 500/1000*600
        self.assertEqual(shots["R"]["image_px"], (200, 300))  # crop (0,100,400,400), centre of it
        self.assertIsNone(shots["G"]["box"])
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("Detect:", "".join(p for p, _ in self.calls))

    def test_legacy_reply_without_a_point_is_recorded(self):
        (row,) = self.run_test(lambda p, b: "I cannot find it on this screen.", legacy=True)
        self.assertEqual(row["shots"]["P"]["note"], "no point in reply")
        self.assertNotIn("R", row["shots"])

    def test_legacy_grid_answer_outside_the_image_skips_refine(self):
        def model(prompt, _b64):
            return '{"x": 5000, "y": 5000}' if "(1000,1000)" in prompt else '{"x": 200, "y": 300}'

        (row,) = self.run_test(model, legacy=True)
        self.assertEqual(row["shots"]["G"]["note"], "outside image")
        self.assertNotIn("R", row["shots"])

    def test_results_json_names_the_mode(self):
        self.run_test(legacy_model, legacy=True)
        data = json.loads((self.dir / "out" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "legacy")


class TestCommandLineAndModelCall(unittest.TestCase):
    def test_defaults_and_flags(self):
        args = aim.parse_args([])
        self.assertEqual(
            (args.model, args.crop, args.zoom, args.quick, args.no_refine, args.legacy),
            ("gemma4", 400, 2, False, False, False),
        )
        args = aim.parse_args(["--quick", "--crop", "300", "--no-refine", "--model", "x", "--legacy"])
        self.assertEqual((args.quick, args.crop, args.no_refine, args.model, args.legacy), (True, 300, True, "x", True))
        args = aim.parse_args(["--tiles", "3", "--pad"])
        self.assertEqual((args.tiles, args.pad), (3, True))
        self.assertEqual((aim.parse_args([]).tiles, aim.parse_args([]).pad), (1, False))

    def test_the_default_targets_file_has_targets(self):
        from desktop_ai_agent.input.geometry import parse_targets

        text = Path(aim.parse_args([]).targets).read_text(encoding="utf-8")
        self.assertGreaterEqual(len(parse_targets(text)), 1)

    def test_make_ask_builds_the_client_as_intended(self):
        made = {}

        class FakeClient:
            def __init__(self, model, host, **kwargs):
                made.update(model=model, host=host, **kwargs)

            def add_user(self, text, images=None):
                made["prompt"], made["images"] = text, images

            def chat(self):
                return mock.Mock(content='[{"box_2d": [1, 2, 3, 4]}]')

        with mock.patch("desktop_ai_agent.core.chat_client.ChatClient", FakeClient):
            reply = aim.make_ask("gemma4", "http://h:1", 4096)("where?", "B64")
        self.assertEqual(reply, '[{"box_2d": [1, 2, 3, 4]}]')
        self.assertEqual(made["images"], ["B64"])
        # Never None: ChatClient maps None to its script/code schema.
        self.assertEqual(made["response_format"], "json")
        self.assertEqual((made["system"], made["normalize"], made["think"]), ("", False, False))
        self.assertEqual(made["options"], {"temperature": 0, "num_ctx": 4096})

    def test_point_schema_is_gone(self):
        self.assertFalse(hasattr(aim, "POINT_SCHEMA"))

    def test_every_keyword_make_ask_uses_exists_on_the_real_chatclient(self):
        from desktop_ai_agent.core.chat_client import ChatClient

        params = inspect.signature(ChatClient.__init__).parameters
        for name in ("think", "response_format", "system", "normalize", "options"):
            self.assertIn(name, params)


if __name__ == "__main__":
    unittest.main()
