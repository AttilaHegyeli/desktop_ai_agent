import threading
import unittest

from desktop_ai_agent.action.executor import Executor
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.service import AgentService


class FakeClient:
    def __init__(self, turns=None, gate=None, boom=False):
        self.turns = list(turns or [ChatTurn(content="hi")])
        self.gate = gate
        self.boom = boom
        self.cleared = False

    def add_user(self, text, images=None):
        pass

    def add_tool_result(self, name, content):
        pass

    def chat(self, tools=None, on_chunk=None):
        if self.gate:
            self.gate.wait(5)
        if self.boom:
            raise RuntimeError("ollama down")
        return self.turns.pop(0)

    def clear(self):
        self.cleared = True


def executor():
    reg = ActionRegistry([ActionSpec("peek", "look", Risk.LOW)])
    return Executor(reg, {"peek": lambda a: "ok"})


class TestService(unittest.TestCase):
    def run_and_wait(self, service, text="go"):
        done = threading.Event()
        box = {}

        def on_done(result):
            box["result"] = result
            done.set()

        self.assertTrue(service.submit(text, on_done=on_done))
        self.assertTrue(done.wait(5), "task did not finish")
        return box["result"]

    def test_runs_task_and_calls_on_done(self):
        result = self.run_and_wait(AgentService(FakeClient(), executor()))
        self.assertEqual((result.status, result.text), ("done", "hi"))

    def test_second_submit_rejected_while_busy_then_allowed(self):
        gate = threading.Event()
        service = AgentService(FakeClient(turns=[ChatTurn(content="a"), ChatTurn(content="b")], gate=gate), executor())
        finished = threading.Event()
        self.assertTrue(service.submit("one", on_done=lambda r: finished.set()))
        self.assertTrue(service.busy)
        self.assertFalse(service.submit("two"))
        gate.set()
        self.assertTrue(finished.wait(5))
        self.assertFalse(service.busy)
        self.assertTrue(service.submit("three"))

    def test_model_crash_becomes_failed_result_and_clears_busy(self):
        service = AgentService(FakeClient(boom=True), executor())
        result = self.run_and_wait(service)
        self.assertEqual(result.status, "failed")
        self.assertIn("ollama down", result.text)
        self.assertFalse(service.busy)

    def test_events_are_forwarded(self):
        turns = [ChatTurn(tool_calls=[{"function": {"name": "peek", "arguments": {}}}]), ChatTurn(content="done")]
        service = AgentService(FakeClient(turns), executor())
        events, done = [], threading.Event()
        service.submit("go", on_event=lambda k, d: events.append(k), on_done=lambda r: done.set())
        self.assertTrue(done.wait(5))
        self.assertEqual(events, ["plan", "result", "final"])

    def test_reset_clears_conversation(self):
        client = FakeClient()
        AgentService(client, executor()).reset()
        self.assertTrue(client.cleared)

    def test_create_builds_default_stack(self):
        service = AgentService.create(audit_path=None)
        self.assertIn("wait", service.executor.registry.names())
        self.assertEqual(service.client.options["temperature"], 0.2)

    def test_create_seeds_system_prompt_and_keeps_it_after_reset(self):
        from desktop_ai_agent.orchestrator.prompts import SYSTEM_PROMPT
        service = AgentService.create(audit_path=None)
        self.assertEqual(service.client.messages[0], {"role": "system", "content": SYSTEM_PROMPT})
        service.reset()
        self.assertEqual(service.client.messages, [{"role": "system", "content": SYSTEM_PROMPT}])




class TestServiceStop(unittest.TestCase):
    def test_stop_aborts_running_task(self):
        gate = threading.Event()
        turn = ChatTurn(tool_calls=[{"function": {"name": "peek", "arguments": {}}}])
        client = FakeClient(turns=[turn], gate=gate)
        client.abort = gate.set  # like ChatClient.abort: unblocks the stream
        service = AgentService(client, executor())
        done = threading.Event()
        box = {}
        self.assertTrue(service.submit("go", on_done=lambda r: (box.update(r=r), done.set())))
        service.stop()
        self.assertTrue(done.wait(5))
        self.assertEqual(box["r"].status, "stopped")
        self.assertFalse(service.busy)

    def test_stop_when_idle_does_nothing(self):
        AgentService(FakeClient(), executor()).stop()

    def test_create_uses_8k_context(self):
        service = AgentService.create(audit_path=None)
        self.assertEqual(service.context_limit, 8192)
        self.assertEqual(service.context_used, 0)

if __name__ == "__main__":
    unittest.main()