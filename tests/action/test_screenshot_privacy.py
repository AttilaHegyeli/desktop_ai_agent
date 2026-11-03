"""The app's own windows are kept out of screenshots, only while the grab runs.
Window handles and the affinity call are faked: no real window is touched."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_ai_agent.action.tools import screenshot as shot_mod

from tests.action.test_screenshot import _install_fake_capture_modules


class TestHiddenOwnWindows(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(sys.modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        _, self.sct = _install_fake_capture_modules()
        self.calls: list[tuple[int, int]] = []
        self.dir = Path(tempfile.mkdtemp())
        for target, value in (
            ("_own_visible_windows", lambda: [11, 22]),
            ("_set_affinity", self.fake_affinity),
        ):
            p = patch.object(shot_mod, target, value)
            p.start()
            self.addCleanup(p.stop)
        sleep = patch.object(shot_mod.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.refuse: set[int] = set()

    def fake_affinity(self, hwnd, mode):
        self.calls.append((hwnd, mode))
        return hwnd not in self.refuse

    def capture(self):
        return shot_mod.capture_screen(self.dir)

    def test_hidden_during_the_grab_and_shown_after(self):
        seen_at_grab = []

        def on_grab(target):
            seen_at_grab.extend(self.calls)
            return self.sct.grab.return_value

        self.sct.grab.side_effect = on_grab
        self.capture()
        hide, show = shot_mod._WDA_EXCLUDEFROMCAPTURE, shot_mod._WDA_NONE
        self.assertEqual(seen_at_grab, [(11, hide), (22, hide)])
        self.assertEqual(self.calls, [(11, hide), (22, hide), (11, show), (22, show)])

    def test_shown_again_when_the_grab_fails(self):
        self.sct.grab.side_effect = OSError("display gone")
        with self.assertRaises(OSError):
            self.capture()
        self.assertEqual(self.calls[-2:], [(11, shot_mod._WDA_NONE), (22, shot_mod._WDA_NONE)])

    def test_a_refused_window_is_not_touched_again(self):
        self.refuse = {22}  # e.g. an older Windows build
        self.capture()
        self.assertEqual([c for c in self.calls if c[0] == 22], [(22, shot_mod._WDA_EXCLUDEFROMCAPTURE)])
        self.assertIn((11, shot_mod._WDA_NONE), self.calls)

    def test_no_own_windows_means_no_wait(self):
        with patch.object(shot_mod, "_own_visible_windows", lambda: []):
            self.capture()
        shot_mod.time.sleep.assert_not_called()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
