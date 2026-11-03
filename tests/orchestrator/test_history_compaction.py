"""Old element lists are replaced by a short note so they stop filling the context window."""
import json
import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatClient, ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import _SUPERSEDED, run_task

STUB = "removed"


class TestCompactToolResults(unittest.TestCase):
    def client(self):
        c = ChatClient("m", system="sys")  # no network: chat() is never called here
        for i in range(3):
            c.add_tool_result("list_elements", f"list {i}")
            c.add_tool_result("wait", f"wait {i}")
        return c

    def contents(self, c, name):
        return [m["content"] for m in c.messages if m.get("tool_name") == name]

    def test_only_the_newest_copy_survives(self):
        c = self.client()
        self.assertEqual(c.compact_tool_results("list_elements", STUB), 2)
        self.assertEqual(self.contents(c, "list_elements"), [STUB, STUB, "list 2"])
        self.assertEqual(self.contents(c, "wait"), ["wait 0", "wait 1", "wait 2"])  # others untouched

    def test_keep_last_and_repeat_calls(self):
        c = self.client()
        c.compact_tool_results("list_elements", STUB, keep_last=2)
        self.assertEqual(self.contents(c, "list_elements"), [STUB, "list 1", "list 2"])
        self.assertEqual(c.compact_tool_results("list_elements", STUB, keep_last=2), 0)  # already done

    def test_nothing_to_do(self):
        c = ChatClient("m", system="sys")
        self.assertEqual(c.compact_tool_results("list_elements", STUB), 0)
        self.assertEqual(c.messages, [{"role": "system", "content": "sys"}])


class ScriptedClient(ChatClient):
    """A real ChatClient (real history) whose model replies are scripted."""

    def __init__(self, turns):
        super().__init__("m", system="sys")
        self.turns = list(turns)

    def chat(self, tools=None, on_chunk=None):
        turn = self.turns.pop(0)
        entry = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            entry["tool_calls"] = turn.tool_calls
        self.messages.append(entry)
        return turn


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


class TestAgentLoopCompaction(unittest.TestCase):
    def test_run_task_keeps_only_the_latest_element_list(self):
        reg = ActionRegistry([
            ActionSpec("list_elements", "list", Risk.LOW,
                       (Param("area", "string", "a", required=False),)),
        ])
        counter = iter(range(100))
        ex = Executor(reg, {"list_elements": lambda a: {"elements": "x" * 2000, "n": next(counter)}})
        client = ScriptedClient([
            ChatTurn(tool_calls=[call("list_elements", area="active")]),
            ChatTurn(tool_calls=[call("list_elements", area="desktop")]),
            ChatTurn(tool_calls=[call("list_elements", area="taskbar")]),
            ChatTurn(content="done"),
        ])
        res = run_task(client, ex, "go", auto_screenshot=False)
        self.assertEqual(res.status, "done")
        lists = [m["content"] for m in client.messages if m.get("tool_name") == "list_elements"]
        self.assertEqual(lists[:2], [_SUPERSEDED["list_elements"]] * 2)
        self.assertEqual(json.loads(lists[2])["output"]["n"], 2)  # the newest one is intact
        self.assertEqual(json.loads(_SUPERSEDED["list_elements"])["status"], "superseded")


class TestEmptyFinalReply(unittest.TestCase):
    def test_an_empty_last_message_becomes_done(self):
        from desktop_ai_agent.orchestrator.agent_loop import EMPTY_REPLY

        reg = ActionRegistry([ActionSpec("peek", "look", Risk.LOW)])
        ex = Executor(reg, {"peek": lambda a: "ok"})
        for content in ("", "  \n"):
            with self.subTest(content=content):
                events = []
                client = ScriptedClient([ChatTurn(tool_calls=[call("peek")]), ChatTurn(content=content)])
                res = run_task(client, ex, "go", auto_screenshot=False,
                               on_event=lambda kind, data: events.append((kind, data)))
                self.assertEqual((res.status, res.text), ("done", EMPTY_REPLY))
                self.assertIn(("final", EMPTY_REPLY), events)  # the UI gets a real reply


if __name__ == "__main__":
    unittest.main()
