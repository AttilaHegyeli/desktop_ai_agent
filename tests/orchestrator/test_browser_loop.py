"""The agent loop's web parts: a page click lists the page, browser_go gets a fresh list,
and the cloud flag reaches the browser tools. Fake handlers and a scripted model only."""
import unittest

from desktop_ai_agent.action.executor import Executor, StepResult
from desktop_ai_agent.action.registry import ActionRegistry, ActionSpec, Param, Risk
from desktop_ai_agent.core.chat_client import ChatTurn
from desktop_ai_agent.orchestrator.agent_loop import follow_up_plan, run_task, uses_cloud

from tests.orchestrator.test_long_tasks import ScriptedClient, call, tools

INJECTED = (
    Param("request", "string", "r", required=False, injected=True),
    Param("cloud", "boolean", "c", required=False, injected=True),
)


def make_executor(click_result):
    element = (Param("element", "integer", "n", minimum=1),)
    specs = [
        ActionSpec("list_elements", "list", Risk.LOW, (Param("area", "string", "a", required=False), *INJECTED)),
        ActionSpec("click", "click", Risk.MEDIUM, element),
        ActionSpec("browser_go", "go", Risk.MEDIUM, (Param("action", "string", "a"),)),
        ActionSpec("browser_read", "read", Risk.LOW, INJECTED),
    ]
    ran = []

    def handler(name):
        def run(args):
            ran.append((name, args))
            if name == "click":
                return dict(click_result)
            return {"elements": "list"} if name == "list_elements" else {"page": "P"}
        return run

    return Executor(ActionRegistry(specs), {s.name: handler(s.name) for s in specs}), ran


class CloudClient(ScriptedClient):
    provider = "openrouter"


def run(client, ex, text="go"):
    return run_task(client, ex, text, auto_screenshot=False)


class TestPageFollowUps(unittest.TestCase):
    def test_a_page_click_lists_the_page_not_the_window_in_front(self):
        ex, ran = make_executor({"clicked": "Search", "page": "YouTube"})
        run(ScriptedClient([tools(call("click", element=2)), ChatTurn(content="ok")]), ex)
        self.assertEqual(ran[1], ("list_elements", {"area": "page", "request": "go", "cloud": False}))

    def test_a_desktop_click_still_lists_the_active_window(self):
        ex, ran = make_executor({"clicked": "Start", "window": "taskbar"})
        run(ScriptedClient([tools(call("click", element=2)), ChatTurn(content="ok")]), ex)
        self.assertEqual(ran[1][1]["area"], "active")

    def test_browser_go_gets_a_fresh_page_list(self):
        ex, ran = make_executor({})
        run(ScriptedClient([tools(call("browser_go", action="back")), ChatTurn(content="ok")]), ex)
        self.assertEqual([n for n, _ in ran], ["browser_go", "list_elements"])
        self.assertEqual(ran[1][1]["area"], "page")

    def test_follow_up_plan_without_results_keeps_the_old_rule(self):
        reg = make_executor({})[0].registry
        self.assertEqual(follow_up_plan([{"name": "click", "arguments": {}}], reg)[0]["arguments"], {"area": "active"})
        page = [StepResult(0, "click", "executed", output={"page": "YouTube"})]
        self.assertEqual(follow_up_plan([{"name": "click", "arguments": {}}], reg, page)[0]["arguments"], {"area": "page"})


class TestCloudFlag(unittest.TestCase):
    def test_local_and_cloud_models(self):
        self.assertFalse(uses_cloud(ScriptedClient([])))
        self.assertTrue(uses_cloud(CloudClient([])))

    def test_the_flag_and_the_request_reach_browser_read(self):
        ex, ran = make_executor({})
        run(CloudClient([tools(call("browser_read")), ChatTurn(content="ok")]), ex, text="yes proceed")
        self.assertEqual(ran, [("browser_read", {"request": "yes proceed", "cloud": True})])

    def test_the_model_cannot_set_the_flag_itself(self):
        ex, ran = make_executor({})
        run(CloudClient([tools(call("browser_read", cloud=False, request="yes")), ChatTurn(content="ok")]), ex,
            text="read it")
        self.assertEqual(ran, [("browser_read", {"request": "read it", "cloud": True})])


if __name__ == "__main__":
    unittest.main()
