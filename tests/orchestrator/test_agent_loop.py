import json
import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import run_task


class FakeClient:
    """Scripted model: returns the queued turns in order and records feedback."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.user = []
        self.tool_msgs = []
        self.tools_seen = None

    def add_user(self, text, images=None):
        self.user.append((text, images))

    def drop_images(self):
        self.user = [(text, None) for text, _imgs in self.user]

    def add_tool_result(self, name, content):
        self.tool_msgs.append((name, json.loads(content)))

    def chat(self, tools=None, on_chunk=None):
        self.tools_seen = tools
        return self.turns.pop(0)


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def tool_turn(*calls):
    return ChatTurn(tool_calls=list(calls))


def make_executor(confirm=None):
    reg = ActionRegistry([
        ActionSpec("peek", "look", Risk.LOW),
        ActionSpec("click", "click", Risk.MEDIUM, (
            Param("x", "integer", "x", minimum=0, maximum=100),
            Param("y", "integer", "y", minimum=0, maximum=100),
        )),
        ActionSpec("delete_file", "delete", Risk.HIGH, (Param("path", "string", "p"),)),
    ])
    ran = []
    handlers = {n: (lambda n: lambda a: ran.append((n, a)) or "ok")(n) for n in ("peek", "click", "delete_file")}
    return Executor(reg, handlers, confirm=confirm), ran


class TestLoop(unittest.TestCase):
    def test_plain_answer_without_tools(self):
        ex, ran = make_executor()
        client = FakeClient([ChatTurn(content="Hello!")])
        res = run_task(client, ex, "hi")
        self.assertEqual((res.status, res.text, res.rounds), ("done", "Hello!", 1))
        self.assertEqual(ran, [])
        self.assertEqual(len(client.tools_seen), 3)  # model got the registry's tools

    def test_tool_call_then_final_answer(self):
        ex, ran = make_executor()
        client = FakeClient([tool_turn(call("peek"), call("click", x=5, y=6)), ChatTurn(content="Done")])
        res = run_task(client, ex, "do it")
        self.assertEqual(res.status, "done")
        self.assertEqual([r[0] for r in ran], ["peek", "click"])
        self.assertEqual([m[1]["status"] for m in client.tool_msgs], ["executed", "executed"])

    def test_rejected_call_is_fed_back_and_model_can_retry(self):
        ex, ran = make_executor()
        client = FakeClient([
            tool_turn(call("press_button")),
            tool_turn(call("peek")),
            ChatTurn(content="ok"),
        ])
        res = run_task(client, ex, "go")
        self.assertEqual(res.status, "done")
        self.assertEqual(client.tool_msgs[0][1]["status"], "rejected")
        self.assertIn("Available", client.tool_msgs[0][1]["error"])
        self.assertEqual(ran, [("peek", {})])

    def test_every_call_gets_a_tool_message_even_when_skipped(self):
        ex, ran = make_executor()
        client = FakeClient([
            tool_turn(call("peek"), call("click", x=999, y=1), call("peek")),
            ChatTurn(content="x"),
        ])
        run_task(client, ex, "go")
        self.assertEqual(len(client.tool_msgs), 3)
        self.assertEqual([m[1]["status"] for m in client.tool_msgs], ["skipped", "rejected", "skipped"])
        self.assertEqual(ran, [], "nothing runs when any step in the plan is invalid")

    def test_gives_up_after_repeated_failures(self):
        ex, _ = make_executor()
        client = FakeClient([tool_turn(call("nope")) for _ in range(10)])
        res = run_task(client, ex, "go", max_failed_rounds=2)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.rounds, 3)

    def test_success_resets_failure_counter(self):
        ex, _ = make_executor()
        client = FakeClient([
            tool_turn(call("nope")), tool_turn(call("nope")), tool_turn(call("peek")),
            tool_turn(call("nope")), tool_turn(call("nope")), ChatTurn(content="fine"),
        ])
        self.assertEqual(run_task(client, ex, "go", max_failed_rounds=2).status, "done")

    def test_declined_high_risk_stops_the_task(self):
        ex, ran = make_executor(confirm=lambda s, a: False)
        client = FakeClient([tool_turn(call("delete_file", path="a.txt")), ChatTurn(content="never reached")])
        res = run_task(client, ex, "delete it")
        self.assertEqual(res.status, "declined")
        self.assertEqual(ran, [])

    def test_approved_high_risk_runs(self):
        ex, ran = make_executor(confirm=lambda s, a: True)
        client = FakeClient([tool_turn(call("delete_file", path="a.txt")), ChatTurn(content="deleted")])
        self.assertEqual(run_task(client, ex, "delete it").status, "done")
        self.assertEqual(ran, [("delete_file", {"path": "a.txt"})])

    def test_max_rounds_stops_endless_tool_use(self):
        ex, _ = make_executor()
        client = FakeClient([tool_turn(call("peek")) for _ in range(20)])
        res = run_task(client, ex, "go", max_rounds=3)
        self.assertEqual((res.status, res.rounds), ("max_rounds", 3))

    def test_events_are_emitted_for_the_ui(self):
        ex, _ = make_executor()
        events = []
        client = FakeClient([tool_turn(call("peek")), ChatTurn(content="done")])
        run_task(client, ex, "go", on_event=lambda k, d: events.append(k))
        self.assertEqual(events, ["plan", "result", "final"])

    def test_images_are_passed_with_the_user_message(self):
        ex, _ = make_executor()
        client = FakeClient([ChatTurn(content="seen")])
        run_task(client, ex, "what is this", images=["B64"])
        self.assertEqual(client.user, [("what is this", ["B64"])])

    def test_successful_screenshot_attaches_image_for_next_turn(self):
        import base64
        import tempfile
        from pathlib import Path

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png)
            reg = ActionRegistry([ActionSpec("screenshot", "capture", Risk.LOW)])
            handlers = {
                "screenshot": lambda a: {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([
                tool_turn(call("screenshot")),
                ChatTurn(content="I see a pixel"),
            ])
            res = run_task(client, ex, "look", auto_screenshot=False)
        self.assertEqual(res.status, "done")
        self.assertEqual(client.user[0], ("look", None))
        self.assertEqual(client.user[1][0], "Screenshot attached.")
        self.assertEqual(len(client.user[1][1]), 1)
        self.assertEqual(base64.b64decode(client.user[1][1][0]), png)

    def test_auto_screenshot_when_user_asks_what_is_on_screen(self):
        import base64
        import tempfile
        from pathlib import Path

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png)
            reg = ActionRegistry([ActionSpec("screenshot", "capture", Risk.LOW)])
            handlers = {
                "screenshot": lambda a: {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([ChatTurn(content="I see the screen")])
            events = []
            res = run_task(
                client, ex, "what do you see on my screen?",
                on_event=lambda k, d: events.append(k),
            )
        self.assertEqual(res.status, "done")
        self.assertEqual(client.user[0][0], "what do you see on my screen?")
        self.assertEqual(base64.b64decode(client.user[0][1][0]), png)
        self.assertIn("screenshot", events)

    def test_no_auto_screenshot_for_plain_chat(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(b"x")
            calls = []
            reg = ActionRegistry([ActionSpec("screenshot", "capture", Risk.LOW)])
            handlers = {
                "screenshot": lambda a: calls.append(1) or {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([ChatTurn(content="Hello!")])
            res = run_task(client, ex, "hi there")
        self.assertEqual(res.status, "done")
        self.assertEqual(calls, [])
        self.assertEqual(client.user, [("hi there", None)])

    def test_auto_screenshot_refreshes_after_ui_changing_tools_for_vision_tasks(self):
        import base64
        import tempfile
        from pathlib import Path

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png)
            reg = ActionRegistry([
                ActionSpec("wait", "pause", Risk.LOW, (
                    Param("seconds", "number", "s", minimum=0, maximum=30),
                )),
                ActionSpec("screenshot", "capture", Risk.LOW),
            ])
            handlers = {
                "wait": lambda a: {"waited_seconds": a["seconds"]},
                "screenshot": lambda a: {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([
                tool_turn(call("wait", seconds=0)),
                ChatTurn(content="refreshed"),
            ])
            res = run_task(client, ex, "look at my screen after a wait")
        self.assertEqual(res.status, "done")
        self.assertEqual(client.user[0][0], "look at my screen after a wait")
        # Fresh capture replaces prior images so only "Current screen." keeps pixels.
        self.assertIsNone(client.user[0][1])
        self.assertEqual(client.user[1][0], "Current screen.")
        self.assertEqual(base64.b64decode(client.user[1][1][0]), png)

    def test_new_vision_input_drops_prior_screenshot(self):
        import base64
        import tempfile
        from pathlib import Path

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png)
            reg = ActionRegistry([ActionSpec("screenshot", "capture", Risk.LOW)])
            handlers = {
                "screenshot": lambda a: {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([
                ChatTurn(content="old answer"),
                ChatTurn(content="new answer"),
            ])
            run_task(client, ex, "look at my screen")
            self.assertIsNotNone(client.user[0][1])
            run_task(client, ex, "what do you see now on my screen?")
        self.assertEqual(client.user[0][0], "look at my screen")
        self.assertIsNone(client.user[0][1])
        self.assertEqual(client.user[1][0], "what do you see now on my screen?")
        self.assertEqual(base64.b64decode(client.user[1][1][0]), png)

    def test_non_vision_input_keeps_prior_screenshot(self):
        client = FakeClient([ChatTurn(content="Hello!")])
        client.add_user("earlier", images=["OLD_B64"])
        ex, _ = make_executor()
        run_task(client, ex, "hi there")
        self.assertEqual(client.user[0], ("earlier", ["OLD_B64"]))
        self.assertEqual(client.user[1], ("hi there", None))

    def test_no_mid_task_refresh_after_web_search_only(self):
        import base64
        import tempfile
        from pathlib import Path

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png)
            shot_calls = []
            reg = ActionRegistry([
                ActionSpec("web_search", "search", Risk.LOW, (
                    Param("query", "string", "q"),
                )),
                ActionSpec("screenshot", "capture", Risk.LOW),
            ])
            handlers = {
                "web_search": lambda a: {"results": []},
                "screenshot": lambda a: shot_calls.append(1)
                or {"path": str(path), "width": 1, "height": 1},
            }
            ex = Executor(reg, handlers)
            client = FakeClient([
                tool_turn(call("web_search", query="x")),
                ChatTurn(content="done"),
            ])
            res = run_task(client, ex, "look at my screen and search x")
        self.assertEqual(res.status, "done")
        # One auto-capture at start; no mid-task refresh after web_search.
        self.assertEqual(len(shot_calls), 1)
        self.assertEqual(len(client.user), 1)


if __name__ == "__main__":
    unittest.main()
