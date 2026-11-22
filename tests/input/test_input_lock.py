import sys
import time
import unittest

from desktop_ai_agent.input.input_lock import VK_ESCAPE, InputLock, Win32HookBackend


class FakeBackend:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self, decide):
        self.started += 1

    def stop(self):
        self.stopped += 1


class InputLockTests(unittest.TestCase):
    def test_blocks_physical_passes_injected(self):
        lock = InputLock(FakeBackend())
        lock.engage()
        self.assertTrue(lock.decide("key", False, 0x41, True))
        self.assertTrue(lock.decide("mouse", False, 0, True))
        self.assertFalse(lock.decide("key", True, 0x41, True))
        self.assertFalse(lock.decide("mouse", True, 0, True))

    def test_not_engaged_passes_everything(self):
        lock = InputLock(FakeBackend())
        self.assertFalse(lock.decide("key", False, 0x41, True))

    def test_release_stops_backend_once(self):
        backend = FakeBackend()
        lock = InputLock(backend)
        lock.engage()
        lock.engage()
        lock.release()
        lock.release()
        self.assertEqual((backend.started, backend.stopped), (1, 1))
        self.assertFalse(lock.is_engaged)

    def test_escape_hold_fires_once(self):
        import threading

        t = [0.0]
        fired = threading.Event()
        lock = InputLock(
            FakeBackend(), on_escape=fired.set, escape_hold_s=3.0, clock=lambda: t[0]
        )
        lock.engage()
        lock.decide("key", False, VK_ESCAPE, True)
        t[0] = 1.0
        lock.decide("key", False, VK_ESCAPE, True)
        self.assertFalse(fired.wait(0.05))
        t[0] = 3.1
        lock.decide("key", False, VK_ESCAPE, True)
        self.assertTrue(fired.wait(1.0))

    def test_escape_release_resets(self):
        import threading

        t = [0.0]
        fired = threading.Event()
        lock = InputLock(
            FakeBackend(), on_escape=fired.set, escape_hold_s=3.0, clock=lambda: t[0]
        )
        lock.engage()
        lock.decide("key", False, VK_ESCAPE, True)
        t[0] = 2.0
        lock.decide("key", False, VK_ESCAPE, False)
        t[0] = 5.0
        lock.decide("key", False, VK_ESCAPE, True)
        self.assertFalse(fired.wait(0.05))


@unittest.skipUnless(sys.platform == "win32", "Win32 hooks")
class Win32HookTests(unittest.TestCase):
    def test_real_hooks_install_and_flag_injected_keys(self):
        """Regression: 64-bit handle truncation made SetWindowsHookEx fail."""
        import ctypes

        seen = []

        def decide(kind, injected, vk, down):
            seen.append((kind, injected, vk, down))
            return False  # never swallow real input in a test

        backend = Win32HookBackend()
        backend.start(decide)
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(0x87, 0, 0, 0)  # VK_F24: no effect on apps
            user32.keybd_event(0x87, 0, 2, 0)
            deadline = time.monotonic() + 2
            while len([e for e in seen if e[2] == 0x87]) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            backend.stop()
        f24 = [e for e in seen if e[0] == "key" and e[2] == 0x87]
        self.assertEqual(len(f24), 2)
        self.assertTrue(all(e[1] for e in f24), "agent-style input must be flagged injected")


if __name__ == "__main__":
    unittest.main()
