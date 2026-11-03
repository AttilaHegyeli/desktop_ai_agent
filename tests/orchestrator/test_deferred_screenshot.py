"""'open X and tell me what you see': no screenshot before X is open.
Fake handlers and a scripted model only: nothing touches the OS."""
import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import opens_first, run_task

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.user = []

    def add_user(self, text, images=None):
        self.user.append((text, images))

    def drop_images(self):
        self.user = [(text, None) for text, _imgs in self.user]

    def add_tool_result(self, name, content):
        pass

    def chat(self, tools=None, on_chunk=None):
        return self.turns.pop(0)


def call(tool, **args):
    return {"function": {"name": tool, "arguments": args}}


class TestOpensFirst(unittest.TestCase):
    def test_opening_requests(self):
        for text in ("open spotify and tell me what you see", "Launch chrome, then describe it",
                     "please open notepad and read this", "can you open paint and look at my screen",
                     "start spotify. what's playing?"):
            with self.subTest(text=text):
                self.assertTrue(opens_first(text))

    def test_other_requests(self):
        for text in ("what do you see?", "which apps are open on my screen", "tell me what is open",
                     "look at my screen then open spotify", "", "opener"):
            with self.subTest(text=text):
                self.assertFalse(opens_first(text))


class TestDeferredCapture(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "shot.png"
        path.write_bytes(PNG)
        self.order = []
        reg = ActionRegistry([
            ActionSpec("screenshot", "capture", Risk.LOW),
            ActionSpec("open_app", "open", Risk.MEDIUM, (Param("name", "string", "n"),)),
        ])
        self.ex = Executor(reg, {
            "screenshot": lambda a: self.order.append("screenshot") or {"path": str(path)},
            "open_app": lambda a: self.order.append("open_app") or {"opened": a["name"]},
        })

    def run_it(self, text):
        client = FakeClient([ChatTurn(tool_calls=[call("open_app", name="spotify")]),
                             ChatTurn(content="I see Spotify.")])
        events = []
        res = run_task(client, self.ex, text, on_event=lambda k, d: events.append((k, d)))
        return res, client, events

    def test_first_capture_waits_for_the_app(self):
        res, client, events = self.run_it("open spotify and tell me what you see")
        self.assertEqual(res.status, "done")
        self.assertEqual(self.order, ["open_app", "screenshot"])  # nothing captured before opening
        self.assertEqual(client.user[0], ("open spotify and tell me what you see", None))
        self.assertEqual(client.user[1][0], "Current screen.")  # the refresh is the first image
        self.assertIn(("screenshot", "deferred"), events)

    def test_plain_vision_request_still_captures_first(self):
        _, _, events = self.run_it("what do you see on my screen")
        self.assertEqual(self.order, ["screenshot", "open_app", "screenshot"])  # start + refresh
        self.assertNotIn(("screenshot", "deferred"), events)


if __name__ == "__main__":
    unittest.main()
