import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import numpy as np

from desktop_ai_agent.remote import call_session
from desktop_ai_agent.remote.call_session import CallController, CallError
from desktop_ai_agent.remote.server import RemoteConfigError, RemoteServer, validate_token

TOKEN = "t" * 32


def pcm(seconds=0.5):
    return (np.ones(int(16000 * seconds)) * 1000).astype("<i2").tobytes()


class Harness:
    def __init__(self, submit=None, transcript="open notepad", clock=None):
        self.events = []
        self.transcript = transcript
        self.submit = submit or (lambda text: ("done", "all good"))
        kwargs = {"clock": clock} if clock else {}
        self.call = CallController(
            transcribe=lambda audio: self.transcript,
            submit=lambda text: self.submit(text),
            synthesize=lambda reply: b"MP3",
            stop_task=lambda: self.events.append("stop"),
            on_start=lambda: self.events.append("start"),
            on_end=lambda r: self.events.append(f"end:{r}"),
            idle_timeout_s=10,
            max_call_s=100,
            **kwargs,
        )
        self.server = RemoteServer(token=TOKEN, call=self.call, port=0)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.bound_port}"

    def req(self, path, body=b"", token=TOKEN, method="POST", headers=None):
        hdrs = {"Authorization": f"Bearer {token}"} if token else {}
        hdrs.update(headers or {})
        r = urllib.request.Request(
            self.base + path, data=body if method == "POST" else None,
            headers=hdrs, method=method,
        )
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def run_utterance(self, audio=None):
        code, body = self.req("/api/utterance", audio or pcm())
        assert code == 200, (code, body)
        job = json.loads(body)["job"]
        since = 0
        for _ in range(50):
            code, body = self.req(f"/api/job/{job}?since={since}&wait=2", method="GET")
            assert code == 200, (code, body)
            snap = json.loads(body)
            since = snap["version"]
            if snap["done"]:
                return snap
        raise AssertionError("job never finished")


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.server.stop()

    def test_weak_token_rejected(self):
        with self.assertRaises(RemoteConfigError):
            validate_token("short")

    def test_auth_required_and_lockout(self):
        self.assertEqual(self.h.req("/api/call/start", token=None)[0], 401)
        for _ in range(4):
            self.assertEqual(self.h.req("/api/call/start", token="wrong")[0], 401)
        self.assertEqual(self.h.req("/api/call/start", token=TOKEN)[0], 429)

    def test_lockout_not_bypassed_by_rotating_forwarded_ip(self):
        for i in range(25):
            self.h.req("/api/call/start", token="wrong",
                       headers={"CF-Connecting-IP": f"10.0.0.{i}"})
        code, _ = self.h.req("/api/call/start", token=TOKEN,
                             headers={"CF-Connecting-IP": "10.9.9.9"})
        self.assertEqual(code, 429)

    def test_page_is_public(self):
        code, body = self.h.req("/", method="GET", token=None)
        self.assertEqual(code, 200)
        self.assertIn(b"Call my PC", body)

    def test_api_needs_auth_for_get(self):
        self.assertEqual(self.h.req("/api/job/x", method="GET", token=None)[0], 401)
        self.assertEqual(self.h.req("/api/audio/x.mp3", method="GET", token=None)[0], 401)

    def test_full_call_flow(self):
        self.assertEqual(self.h.req("/api/utterance", pcm())[0], 409)  # no call yet
        self.assertEqual(self.h.req("/api/call/start")[0], 200)
        self.assertEqual(self.h.req("/api/call/start")[0], 409)  # one call at a time
        snap = self.h.run_utterance()
        self.assertEqual(snap["transcript"], "open notepad")
        self.assertEqual(snap["reply"], "all good")
        self.assertEqual(snap["status"], "done")
        code, audio = self.h.req(snap["audio_url"], method="GET")
        self.assertEqual((code, audio), (200, b"MP3"))
        self.assertEqual(self.h.req("/api/call/end")[0], 200)
        self.assertEqual(self.h.events, ["start", "stop", "end:hangup"])
        # nothing survives the call
        self.assertEqual(self.h.req(snap["audio_url"], method="GET")[0], 404)

    def test_busy_agent_reported_in_job(self):
        self.h.submit = lambda text: None
        self.h.req("/api/call/start")
        snap = self.h.run_utterance()
        self.assertEqual(snap["status"], "busy")
        self.assertTrue(snap["error"])

    def test_empty_speech(self):
        self.h.transcript = ""
        self.h.req("/api/call/start")
        snap = self.h.run_utterance()
        self.assertEqual((snap["status"], snap["reply"]), ("empty", ""))

    def test_long_task_does_not_hold_the_request(self):
        gate = threading.Event()
        self.h.submit = lambda text: (gate.wait(5), ("done", "late"))[1]
        self.h.req("/api/call/start")
        code, body = self.h.req("/api/utterance", pcm())
        self.assertEqual(code, 200)  # returns immediately, task still running
        job = json.loads(body)["job"]
        # second utterance while the first is running is refused
        self.assertEqual(self.h.req("/api/utterance", pcm())[0], 409)
        t0 = time.monotonic()
        _, body = self.h.req(f"/api/job/{job}?since=999&wait=0.3", method="GET")
        self.assertLess(time.monotonic() - t0, 2)
        self.assertFalse(json.loads(body)["done"])
        gate.set()

    def test_stop_endpoint_reaches_agent(self):
        self.h.req("/api/call/start")
        self.assertEqual(self.h.req("/api/stop")[0], 200)
        self.assertIn("stop", self.h.events)

    def test_odd_length_audio_rejected(self):
        self.h.req("/api/call/start")
        self.assertEqual(self.h.req("/api/utterance", b"\x00\x01\x02")[0], 400)

    def test_oversized_body_rejected(self):
        self.h.req("/api/call/start")
        self.assertEqual(self.h.req("/api/utterance", b"\x00" * 1_100_000)[0], 413)

    def test_unknown_job_404(self):
        self.h.req("/api/call/start")
        self.assertEqual(self.h.req("/api/job/nope", method="GET")[0], 404)


class CallLogicTests(unittest.TestCase):
    def test_idle_timeout_ends_call_and_releases(self):
        t = [0.0]
        h = Harness(clock=lambda: t[0])
        try:
            h.call.start()
            t[0] = 5
            h.call.check_timeouts()
            self.assertTrue(h.call.active)
            t[0] = 11
            h.call.check_timeouts()
            self.assertFalse(h.call.active)
            self.assertIn("end:timeout", h.events)
        finally:
            h.server.stop()

    def test_heartbeat_keeps_call_alive(self):
        t = [0.0]
        h = Harness(clock=lambda: t[0])
        try:
            h.call.start()
            t[0] = 8
            h.call.heartbeat()
            t[0] = 15
            h.call.check_timeouts()
            self.assertTrue(h.call.active)
        finally:
            h.server.stop()

    def test_max_duration_ends_call(self):
        t = [0.0]
        h = Harness(clock=lambda: t[0])
        try:
            h.call.start()
            t[0] = 5
            h.call.heartbeat()
            t[0] = 101
            h.call.check_timeouts()
            self.assertFalse(h.call.active)
            self.assertIn("end:max_duration", h.events)
        finally:
            h.server.stop()

    def test_failed_on_start_leaves_no_call(self):
        events = []

        def boom():
            raise RuntimeError("hooks failed")

        call = CallController(
            transcribe=lambda a: "", submit=lambda t: None, synthesize=lambda r: b"",
            on_start=boom, on_end=lambda r: events.append(r),
        )
        with self.assertRaises(RuntimeError):
            call.start()
        self.assertFalse(call.active)
        self.assertEqual(events, ["error"])  # lock released on the failure path

    def test_stale_hangup_timer_cannot_end_a_newer_call(self):
        h = Harness(transcript="goodbye")
        old = call_session.HANGUP_DELAY_S
        call_session.HANGUP_DELAY_S = 0.4
        try:
            h.call.start()
            snap = h.call.poll_job(h.call.submit_utterance(pcm()), 0, 0)
            deadline = time.monotonic() + 3
            while not snap["done"] and time.monotonic() < deadline:
                snap = h.call.poll_job(
                    next(iter(h.call._jobs)), snap["version"], 1.0
                )
            self.assertEqual(snap["status"], "hangup")
            h.call.end("hangup")          # phone hung up itself
            h.call.start()                # new call within the delay
            time.sleep(0.8)               # old timer fires now
            self.assertTrue(h.call.active)
        finally:
            call_session.HANGUP_DELAY_S = old
            h.server.stop()

    def test_stop_without_call_is_409(self):
        h = Harness()
        try:
            with self.assertRaises(CallError):
                h.call.stop_task()
        finally:
            h.server.stop()


if __name__ == "__main__":
    unittest.main()
