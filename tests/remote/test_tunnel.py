import io
import threading
import unittest

from desktop_ai_agent.remote.server import RemoteConfigError, validate_token
from desktop_ai_agent.remote.tunnel import TunnelManager, extract_url


class FakeProc:
    def __init__(self, text):
        self.stdout = io.StringIO(text)
        self.terminated = False

    def poll(self):
        return 0 if self.stdout.closed or self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class ExtractUrlTests(unittest.TestCase):
    def test_finds_tunnel_url(self):
        line = "2026-11-21T14:50:06Z INF |  https://channels-poems-operator-labels.trycloudflare.com  |"
        self.assertEqual(extract_url(line), "https://channels-poems-operator-labels.trycloudflare.com")

    def test_ignores_api_host(self):
        self.assertIsNone(extract_url("Requesting at https://api.trycloudflare.com/tunnel"))

    def test_no_url(self):
        self.assertIsNone(extract_url("INF Registered tunnel connection"))


class TunnelManagerTests(unittest.TestCase):
    def test_reports_url_and_stops(self):
        urls, done = [], threading.Event()
        output = "INF Requesting\nINF |  https://abc-def.trycloudflare.com |\nINF Registered\n"
        proc = FakeProc(output)

        def on_url(u):
            urls.append(u)
            if u:
                done.set()

        mgr = TunnelManager(
            8765, on_url=on_url, on_log=lambda m: None,
            exe=__file__, popen=lambda *a, **k: proc,
        )
        self.assertTrue(mgr.start())
        self.assertTrue(done.wait(2))
        self.assertEqual(urls[0], "https://abc-def.trycloudflare.com")
        mgr.stop()
        self.assertTrue(proc.terminated)

    def test_missing_cloudflared_is_reported(self):
        logs = []
        import desktop_ai_agent.remote.tunnel as t
        original = t.find_cloudflared
        t.find_cloudflared = lambda configured=None: None
        try:
            mgr = TunnelManager(8765, on_url=lambda u: None, on_log=logs.append)
            self.assertFalse(mgr.start())
        finally:
            t.find_cloudflared = original
        self.assertIn("cloudflared not found", logs[0])


class PasswordTests(unittest.TestCase):
    def test_min_length(self):
        with self.assertRaises(RemoteConfigError):
            validate_token("short")
        self.assertEqual(validate_token("  my own password  "), "my own password")
