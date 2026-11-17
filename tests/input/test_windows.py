"""Tests for input/windows.py with fake rows: no window is enumerated or touched."""
import unittest

from desktop_ai_agent.input.windows import WindowInfo, parse_windows, program_name


def row(hwnd=1, title="Notes - Notepad", pid=10, exe=r"C:\Windows\notepad.exe", **flags):
    base = {"hwnd": hwnd, "title": title, "cls": "Notepad", "pid": pid, "exe": exe, "visible": True,
            "minimized": False, "maximized": False, "tool": False, "owned": False, "cloaked": False}
    return base | flags


class TestParseWindows(unittest.TestCase):
    def test_keeps_user_windows_in_order(self):
        wins = parse_windows([row(1), row(2, "Spotify Premium", 20, r"C:\x\Spotify.exe", minimized=True)])
        self.assertEqual(wins, [
            WindowInfo(1, "Notes - Notepad", "notepad", 10),
            WindowInfo(2, "Spotify Premium", "Spotify", 20, minimized=True),
        ])

    def test_drops_what_alt_tab_would_not_show(self):
        rows = [
            row(1, visible=False),
            row(2, title="   "),
            row(3, tool=True),
            row(4, owned=True),  # e.g. a "Save changes?" dialog: it belongs to its app
            row(5, cloaked=True),
            row(6, cls="Progman", title="Program Manager"),
            row(7, cls="Shell_TrayWnd"),
            row(8, pid=99),  # ours
            row(9),
        ]
        self.assertEqual([w.hwnd for w in parse_windows(rows, own_pid=99)], [9])

    def test_drops_malformed_rows(self):
        rows = ["junk", {"title": "x"}, row(hwnd=0), row(hwnd=True), row(pid="10"), row(5)]
        self.assertEqual([w.hwnd for w in parse_windows(rows)], [5])

    def test_cleans_titles_and_survives_an_unknown_program(self):
        (win,) = parse_windows([row(title=" Notes \n - Notepad ", exe="")])
        self.assertEqual((win.title, win.program), ("Notes - Notepad", ""))

    def test_program_name(self):
        self.assertEqual(program_name(r"C:\Program Files\Microsoft\Edge\msedge.exe"), "msedge")
        self.assertEqual(program_name(""), "")


if __name__ == "__main__":
    unittest.main()
