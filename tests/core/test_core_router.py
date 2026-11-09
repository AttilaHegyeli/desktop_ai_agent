import unittest

from desktop_ai_agent.core.core_router import CoreRouter
from desktop_ai_agent.core.openrouter_client import OpenRouterClient
from desktop_ai_agent.orchestrator.prompts import DESKTOP_AGENT_PROMPT, SYSTEM_PROMPT


SETUP = {
    "ollama": {"base_model": "gemma4"},
    "core_ai": {
        "selected": "openrouter:openai/gpt-4o",
        "openrouter": {
            "fallback_model": "google/gemini-2.5-flash",
            "models": [
                {"id": "openai/gpt-4o", "label": "GPT-4o", "context_tokens": 128000},
                {
                    "id": "google/gemini-2.5-flash",
                    "label": "Gemini 2.5 Flash",
                    "context_tokens": 1048576,
                },
            ],
        },
    },
}


class TestCoreRouter(unittest.TestCase):
    def test_api_uses_shared_prompt_local_uses_gemma_extras(self):
        router = CoreRouter(
            setup=SETUP,
            local_host="http://127.0.0.1:11434",
            api_key="sk-test",
            selection="openrouter:openai/gpt-4o",
        )
        self.assertIsInstance(router._client, OpenRouterClient)
        self.assertEqual(router._client.system, DESKTOP_AGENT_PROMPT)
        self.assertNotIn("Local-model extras", router._client.system)

        info = router.escalate("continue here", reason="context")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["tier"], "fallback_api")
        self.assertIn("gemini-2.5-flash", info["selection"])

        info2 = router.escalate("continue again", reason="context")
        self.assertIsNotNone(info2)
        assert info2 is not None
        self.assertEqual(info2["tier"], "local")
        self.assertEqual(router._client.system, SYSTEM_PROMPT)
        self.assertIn("Local-model extras", router._client.system)

        self.assertIsNone(router.escalate("again", reason="context"))

    def test_missing_key_falls_back_to_local(self):
        router = CoreRouter(
            setup=SETUP,
            local_host="http://127.0.0.1:11434",
            api_key=None,
            selection="openrouter:openai/gpt-4o",
        )
        self.assertEqual(router.provider, "local")
        self.assertEqual(router.model, "gemma4")

    def test_billing_error_skips_other_api_and_goes_local(self):
        router = CoreRouter(
            setup=SETUP,
            local_host="http://127.0.0.1:11434",
            api_key="sk-test",
            selection="openrouter:openai/gpt-4o",
        )
        info = router.escalate(
            "continue",
            reason="api_error:key_limit",
            skip_api_fallback=True,
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["tier"], "local")
        self.assertIn("gemma4", info["selection"])


if __name__ == "__main__":
    unittest.main()

