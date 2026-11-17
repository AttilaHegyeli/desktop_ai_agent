from desktop_ai_agent.input.hotkey import HoldKey


class Fake:
    def __init__(self):
        self.t = 0.0
        self.key = False
        self.other = False

    def make(self, hold_ms=250):
        return HoldKey(
            hold_ms=hold_ms,
            key_down=lambda: self.key,
            other_down=lambda: self.other,
            clock=lambda: self.t,
        )


def test_press_after_hold_then_release():
    f = Fake()
    k = f.make()
    f.key = True
    assert k.poll() is None
    f.t = 0.3
    assert k.poll() == "press"
    assert k.poll() is None
    f.key = False
    assert k.poll() == "release"


def test_short_tap_does_nothing():
    f = Fake()
    k = f.make()
    f.key = True
    k.poll()
    f.t = 0.1
    f.key = False
    assert k.poll() is None


def test_shortcut_with_other_key_is_ignored():
    f = Fake()
    k = f.make()
    f.key = True
    k.poll()
    f.other = True
    f.t = 0.1
    assert k.poll() is None
    f.other = False
    f.t = 1.0
    assert k.poll() is None  # tainted until release
    f.key = False
    assert k.poll() is None
