from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may start, find or reach a real browser (AGENT.MD rule 8).

    The browser session gets an opener that always refuses, the default-browser lookup
    reads nothing, and page_control's window helpers see no windows. Tests that need a
    browser give page_control a fake session of their own.
    """
    from desktop_ai_agent.action.tools import browser_session, page_control

    def refuse(launch: bool, old: object) -> object:
        raise browser_session.NotRunning("tests never open a real browser")

    monkeypatch.setattr(browser_session, "_read_default_command", lambda: "")
    monkeypatch.setattr(browser_session, "SESSION", browser_session.BrowserSession(opener=refuse))
    monkeypatch.setattr(page_control, "_windows", lambda: [])
    monkeypatch.setattr(page_control, "_address_of", lambda hwnd: None)
    monkeypatch.setattr(page_control, "_bring_to_front", lambda hwnd: False)
    page_control.reset()


@pytest.fixture
def sample_entries() -> list[dict[str, str]]:
    return [
        {
            "name": "steam",
            "display": "Steam",
            "path": r"C:\Program Files (x86)\Steam\steam.exe",
            "folder": r"C:\Program Files (x86)\Steam",
            "root": r"C:\Program Files (x86)",
        },
        {
            "name": "git",
            "display": "Git",
            "path": r"C:\Program Files\Git\bin\git.exe",
            "folder": r"C:\Program Files\Git",
            "root": r"C:\Program Files",
        },
        {
            "name": "docker",
            "display": "Docker",
            "path": r"C:\Program Files\Docker",
            "folder": r"C:\Program Files\Docker",
            "root": r"C:\Program Files",
        },
    ]


@pytest.fixture
def fake_program_files(tmp_path: Path) -> Path:
    """
    Fake Windows-like tree:
      <drive>/Program Files/Steam/steam.exe
      <drive>/Program Files/Git/bin/git.exe
      <drive>/Program Files (x86)/Notepad++/notepad++.exe
    """
    drive = tmp_path / "Z"
    pf = drive / "Program Files"
    pf86 = drive / "Program Files (x86)"

    steam = pf / "Steam"
    steam.mkdir(parents=True)
    (steam / "steam.exe").write_bytes(b"MZ")
    (steam / "uninstall.exe").write_bytes(b"MZ")

    git = pf / "Git" / "bin"
    git.mkdir(parents=True)
    (git / "git.exe").write_bytes(b"MZ")

    npp = pf86 / "Notepad++"
    npp.mkdir(parents=True)
    (npp / "notepad++.exe").write_bytes(b"MZ")

    orphan = pf / "SomeVendor"
    orphan.mkdir(parents=True)
    (orphan / "helper.exe").write_bytes(b"MZ")

    return drive
