"""Long tasks: automatic follow-up steps, stuck detection and the chat handoff.
Everything runs on fake handlers and a scripted model: nothing touches the OS."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatClient, ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import (
    MAX_HANDOFFS,
    STUCK_HINT,
    follow_up_plan,
    handoff_note,
    run_task,
)
from desktop_ai_agent.orchestrator.task_state import TaskState


class ScriptedClient(ChatClient):
    """A real ChatClient (real history) whose model replies are scripted.

    `fill` sets context_used after every reply, as Ollama's token count would.
    """

    def __init__(self, turns, num_ctx=8192, fill=0):
        super().__init__("m", system="sys", options={"num_ctx": num_ctx})
        self.turns = list(turns)
        self.fill = fill
        self.sent = []  # the history each chat() call saw

    def chat(self, tools=None, on_chunk=None):
        self.sent.append([dict(m) for m in self.messages])
        turn = self.turns.pop(0)
        entry = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            entry["tool_calls"] = turn.tool_calls
        self.messages.append(entry)
        self.context_used = self.fill
        return turn


def call(fn, **args):
    return {"function": {"name": fn, "arguments": args}}


def tools(*calls):
    return ChatTurn(tool_calls=list(calls))


def make_executor(fail=(), extra=()):
    """Registry with the UI actions; handlers record what ran. Names in `fail` raise."""
    element = (Param("element", "integer", "n", minimum=1),)
    specs = [
        ActionSpec("list_elements", "list", Risk.LOW, (Param("area", "string", "a", required=False),)),
        ActionSpec("click", "click", Risk.MEDIUM, element),
        ActionSpec("type_text", "type", Risk.MEDIUM, element + (Param("text", "string", "t"),)),
        ActionSpec("open_app", "open", Risk.MEDIUM, (Param("name", "string", "n"),)),
        ActionSpec("wait", "wait", Risk.LOW, (Param("seconds", "number", "s", minimum=0, maximum=30),)),
        *extra,
    ]
    ran = []

    def handler(name):
        def run(args):
            ran.append((name, args))
            if name in fail:
                raise RuntimeError(f"{name} broke")
            if name == "list_elements":
                return {"elements": f"list #{len(ran)}"}
            return "ok"
        return run

    return Executor(ActionRegistry(specs), {s.name: handler(s.name) for s in specs}), ran


def run(client, ex, text="go", **kw):
    events = []
    kw.setdefault("auto_screenshot", False)
    res = run_task(client, ex, text, on_event=lambda k, d: events.append((k, d)), **kw)
    return res, events


class TestFollowUpPlan(unittest.TestCase):
    def setUp(self):
        self.reg = make_executor()[0].registry

    def names(self, *plan_names):
        plan = [{"name": n, "arguments": {}} for n in plan_names]
        return [s["name"] for s in follow_up_plan(plan, self.reg)]

    def test_rules(self):
        self.assertEqual(self.names("click"), ["list_elements"])
        self.assertEqual(self.names("type_text"), ["list_elements"])
        self.assertEqual(self.names("open_app"), [])  # "open X" is often the whole task
        self.assertEqual(self.names("wait"), [])  # not UI-changing by itself

    def test_model_already_listed_after_the_change(self):
        self.assertEqual(self.names("click", "list_elements"), [])
        self.assertEqual(self.names("list_elements", "click"), ["list_elements"])  # list went stale

    def test_needs_list_elements_in_the_registry(self):
        reg = ActionRegistry([ActionSpec("click", "c", Risk.MEDIUM)])
        self.assertEqual(follow_up_plan([{"name": "click", "arguments": {}}], reg), [])


class TestAutoFollowUp(unittest.TestCase):
    def test_click_gets_a_fresh_list_in_the_same_round(self):
        ex, ran = make_executor()
        client = ScriptedClient([tools(call("click", element=3)), ChatTurn(content="clicked")])
        res, events = run(client, ex)
        self.assertEqual((res.status, res.rounds), ("done", 2))
        self.assertEqual([n for n, _ in ran], ["click", "list_elements"])
        self.assertIn(("follow_up", [{"name": "list_elements", "arguments": {"area": "active"}}]), events)
        # The model's second request already contains the fresh list.
        history = client.sent[1]
        assistant = next(m for m in history if m["role"] == "assistant")
        called = [c["function"]["name"] for c in assistant["tool_calls"]]
        answered = [m["tool_name"] for m in history if m["role"] == "tool"]
        self.assertEqual(called, ["click", "list_elements"])
        self.assertEqual(answered, called)  # every call has exactly one answer, in order

    def test_open_app_gets_no_follow_up(self):
        ex, ran = make_executor()
        client = ScriptedClient([tools(call("open_app", name="Paint")), ChatTurn(content="open")])
        _, events = run(client, ex)
        self.assertEqual(ran, [("open_app", {"name": "Paint"})])
        self.assertNotIn("follow_up", [k for k, _ in events])

    def test_open_app_still_makes_an_old_list_stale(self):
        ex, _ = make_executor()
        client = ScriptedClient(
            [tools(call("list_elements", area="active"), call("open_app", name="Paint")),
             ChatTurn(content="done")],
            num_ctx=1000,
            fill=900,
        )
        run(client, ex)
        note = client.sent[1][1]["content"]
        self.assertNotIn("Latest list_elements result", note)

    def test_no_follow_up_after_a_failure(self):
        ex, ran = make_executor(fail={"click"})
        client = ScriptedClient([tools(call("click", element=3)), ChatTurn(content="failed")])
        run(client, ex)
        self.assertEqual([n for n, _ in ran], ["click"])

    def test_can_be_turned_off(self):
        ex, ran = make_executor()
        client = ScriptedClient([tools(call("click", element=3)), ChatTurn(content="ok")])
        run(client, ex, auto_follow_up=False)
        self.assertEqual([n for n, _ in ran], ["click"])

    def test_fresh_list_replaces_the_auto_screenshot(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        png = tmp / "s.png"
        png.write_bytes(b"png")
        shot = ActionSpec("screenshot", "shot", Risk.LOW)
        ex, ran = make_executor(extra=(shot,))
        ex._handlers["screenshot"] = lambda a: ran.append(("screenshot", a)) or {"path": str(png)}
        client = ScriptedClient([tools(call("click", element=3)), ChatTurn(content="ok")])
        run(client, ex, images=["b64"], auto_screenshot=True)  # vision task
        self.assertNotIn("screenshot", [n for n, _ in ran])


class TestStuck(unittest.TestCase):
    def test_hint_when_the_same_failing_call_repeats(self):
        ex, _ = make_executor(fail={"click"})
        client = ScriptedClient([
            tools(call("click", element=3)),
            tools(call("click", element=3)),
            ChatTurn(content="giving up"),
        ])
        res, events = run(client, ex)
        self.assertEqual(res.status, "done")
        stuck = [d for k, d in events if k == "stuck"]
        self.assertEqual(len(stuck), 1)
        self.assertIn('"element": 3', stuck[0][0])
        hints = [m["content"] for m in client.messages if m["role"] == "user" and "already tried" in m["content"]]
        self.assertEqual(len(hints), 1)
        self.assertTrue(hints[0].startswith(STUCK_HINT.split("{")[0]))

    def test_a_different_call_is_not_a_repeat(self):
        ex, _ = make_executor(fail={"click"})
        client = ScriptedClient([
            tools(call("click", element=3)),
            tools(call("click", element=4)),
            ChatTurn(content="ok"),
        ])
        _, events = run(client, ex)
        self.assertNotIn("stuck", [k for k, _ in events])


class TestHandoff(unittest.TestCase):
    def test_restarts_the_chat_with_a_note_when_context_fills(self):
        ex, _ = make_executor()
        client = ScriptedClient(
            [tools(call("click", element=3)), ChatTurn(content="done")], num_ctx=1000, fill=900
        )
        res, events = run(client, ex, "open the 3rd song")
        self.assertEqual(res.status, "done")
        self.assertIn(("handoff", 1), events)
        fresh = client.sent[1]
        self.assertEqual([m["role"] for m in fresh], ["system", "user"])  # old history is gone
        note = fresh[1]["content"]
        self.assertIn("Original request: open the 3rd song", note)
        self.assertIn("click(", note)
        self.assertIn("Latest list_elements result", note)  # the auto list is still valid

    def test_handoff_sends_no_screenshot(self):
        ex, ran = make_executor(extra=(ActionSpec("screenshot", "shot", Risk.LOW),))
        client = ScriptedClient(
            [tools(call("click", element=3)), ChatTurn(content="done")], num_ctx=1000, fill=900
        )
        _, events = run(client, ex, images=["b64"], auto_screenshot=True)  # a vision task
        self.assertIn(("handoff", 1), events)
        self.assertNotIn("images", client.sent[1][1])
        self.assertNotIn("screenshot", [n for n, _ in ran])

    def test_no_handoff_below_the_threshold(self):
        ex, _ = make_executor()
        client = ScriptedClient(
            [tools(call("click", element=3)), ChatTurn(content="done")], num_ctx=1000, fill=100
        )
        _, events = run(client, ex)
        self.assertNotIn("handoff", [k for k, _ in events])

    def test_no_new_steps_means_no_second_handoff(self):
        ex, _ = make_executor()
        client = ScriptedClient(
            [tools(call("wait", seconds=0)), ChatTurn(content="hmm"), ChatTurn(content="done")],
            num_ctx=1000,
            fill=900,
        )
        client.turns[1] = ChatTurn(content="I will now click it")  # nudge round: no new steps
        _, events = run(client, ex)
        self.assertEqual([d for k, d in events if k == "handoff"], [1])

    def test_handoffs_are_capped(self):
        ex, _ = make_executor()
        turns = [tools(call("wait", seconds=0)) for _ in range(MAX_HANDOFFS + 2)] + [ChatTurn(content="done")]
        client = ScriptedClient(turns, num_ctx=1000, fill=900)
        res, events = run(client, ex)
        self.assertEqual(res.status, "done")
        self.assertEqual(len([k for k, _ in events if k == "handoff"]), MAX_HANDOFFS)


class TestHandoffNote(unittest.TestCase):
    def test_keeps_the_newest_steps_and_drops_stale_lists(self):
        state = TaskState("do many things", steps=[f"step {i}" for i in range(20)])
        note = handoff_note(state)
        self.assertIn("(5 earlier steps not shown)", note)
        self.assertIn("step 19", note)
        self.assertNotIn("- step 4\n", note)
        self.assertNotIn("list_elements result", note)

    def test_a_click_without_a_new_list_invalidates_the_old_one(self):
        ex, _ = make_executor()
        client = ScriptedClient([
            tools(call("list_elements", area="active"), call("click", element=1)),
            ChatTurn(content="done"),
        ])
        events = []
        run_task(client, ex, "go", auto_screenshot=False, auto_follow_up=False,
                 on_event=lambda k, d: events.append((k, d)))
        # Rebuild the state the loop kept: the list came BEFORE the click, so it is stale.
        state = TaskState("go")
        from desktop_ai_agent.orchestrator.agent_loop import _remember
        for kind, r in events:
            if kind == "result":
                _remember(state, {"name": r.name, "arguments": {}}, r)
        self.assertIsNone(state.last_elements)


class TestAutoToolCalls(unittest.TestCase):
    def test_appends_to_the_latest_assistant_message(self):
        c = ChatClient("m", system="sys")
        c.messages.append({"role": "assistant", "content": "", "tool_calls": [call("click", element=1)]})
        c.add_tool_result("click", json.dumps({"status": "executed"}))
        c.add_auto_tool_calls([call("list_elements", area="active")])
        names = [x["function"]["name"] for x in c.messages[1]["tool_calls"]]
        self.assertEqual(names, ["click", "list_elements"])


if __name__ == "__main__":
    unittest.main()
