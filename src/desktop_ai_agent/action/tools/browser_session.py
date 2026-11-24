"""The agent's own browser: one long-lived connection, owned by one thread.

What it does
  - finds the user's default browser (Windows registry); it must be Chromium-based
    (Chrome, Edge, Opera, Brave, Vivaldi...). There is no fallback (owner's decision).
  - starts it with the agent's OWN profile, never the user's everyday one, plus a
    control port on 127.0.0.1, or reconnects to the window an earlier run left open.
    The browser stays open when the app closes; `session.json` next to the profile
    keeps the port so the next start reconnects.
  - tracks the tabs: the "current" tab is the one the agent works in, and a tab that a
    click opens becomes the current one.

Why one thread: Playwright's objects only work on the thread that created them, and
every agent task runs on a new thread. So all browser work goes through
``SESSION.call(fn)``: ``fn(live)`` runs on the browser thread and only plain data
(strings, numbers, dicts) comes back. No Playwright object ever leaves this thread.

Windows and process calls are thin functions; the tests replace them (and the whole
opener) so no real browser is ever started there.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

USER_CHOICE_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
# Default browsers the agent cannot drive (not Chromium-based).
NON_CHROMIUM = frozenset({
    "firefox", "waterfox", "librewolf", "floorp", "iexplore", "palemoon", "seamonkey", "safari",
})
LAUNCH_ARGS = ("--no-first-run", "--no-default-browser-check")
START_TIMEOUT_SECONDS = 25.0  # Opera GX needed ~6 s for its port on the owner's PC
CALL_TIMEOUT_SECONDS = 45.0
GOTO_TIMEOUT_MS = 15000
BLANK_URLS = ("about:blank", "")


class BrowserError(RuntimeError):
    """A problem the model should report to the user (the message says what to do)."""


class NotRunning(BrowserError):
    """The agent's browser is not open, and the caller asked not to start it."""


@dataclass(frozen=True)
class BrowserInfo:
    exe: str
    program: str  # executable name without .exe, lower case: "opera", "chrome", "msedge"


# ---- pure helpers ------------------------------------------------------------------

def exe_from_command(command: str) -> str:
    """'"C:\\x\\opera.exe" -noautoupdate -- "%1"' -> 'C:\\x\\opera.exe' (env vars expanded)."""
    text = os.path.expandvars((command or "").strip())
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text[1:]
    return text.split(" ", 1)[0]


def check_browser(exe: str) -> BrowserInfo:
    """BrowserInfo for `exe`, or BrowserError when it cannot be the agent's browser."""
    if not exe:
        raise BrowserError("No default web browser is set in Windows. Ask the user to set one.")
    program = Path(exe).stem.casefold()
    if program in NON_CHROMIUM:
        raise BrowserError(
            f"The default browser ({Path(exe).stem}) is not Chromium-based. The agent's browser tools "
            "need Chrome, Edge, Opera, Brave or Vivaldi as the default browser: tell the user."
        )
    return BrowserInfo(exe, program)


def profile_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "desktop_ai_agent" / "browser"


def paths_for(info: BrowserInfo, root: Path | None = None) -> tuple[Path, Path]:
    """(profile folder, session file) for one browser program. One profile per program:
    an Opera profile cannot be opened by Chrome."""
    folder = (root or profile_root()) / info.program
    return folder / "profile", folder / "session.json"


def read_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("port"), int):
        return None
    return data


def write_session(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---- the live connection (worker thread only) --------------------------------------

@dataclass
class Live:
    """Everything Playwright-side. Only ever touched on the browser thread."""

    playwright: Any
    browser: Any
    context: Any
    info: BrowserInfo
    pid: int
    current: Any = None
    _keys: dict[int, int] = field(default_factory=dict)  # id(page) -> stable key
    _next_key: int = 1

    @property
    def connected(self) -> bool:
        try:
            return bool(self.browser.is_connected())
        except Exception:  # noqa: BLE001 - a dead connection may raise instead
            return False

    def pages(self) -> list[Any]:
        return [p for p in list(self.context.pages) if not p.is_closed()]

    def page(self) -> Any:
        """The tab the agent works in: the current one, else the newest open tab."""
        if self.current is not None and not self.current.is_closed():
            return self.current
        pages = self.pages()
        self.current = pages[-1] if pages else None
        return self.current

    def key(self, page: Any) -> int:
        k = self._keys.get(id(page))
        if k is None:
            k = self._keys[id(page)] = self._next_key
            self._next_key += 1
        return k

    def page_by_key(self, key: int) -> Any:
        return next((p for p in self.pages() if self.key(p) == key), None)

    def set_current(self, page: Any) -> None:
        self.current = page

    def follow_new(self, before: list[Any]) -> Any:
        """After an action: the newest tab that was not open `before`, made current."""
        old = {id(p) for p in before}
        new = [p for p in self.pages() if id(p) not in old]
        if not new:
            return None
        self.current = new[-1]
        return self.current

    def new_tab(self, url: str) -> tuple[Any, bool]:
        """Open `url` in a new tab, or in a blank tab (a just-started browser has one, and
        a left-over blank tab made the window show "about:blank" in the owner's test).
        Returns (page, loaded): loaded is False when the page was still loading at the
        timeout (it stays open and keeps loading)."""
        blank = next((p for p in self.pages() if p.url in BLANK_URLS), None)
        page = blank or self.context.new_page()
        self.current = page
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
            return page, True
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "TimeoutError":
                return page, False
            raise


# ---- the session (any thread) ------------------------------------------------------

Opener = Callable[[bool, "Live | None"], Live]


class BrowserSession:
    """Hands browser work to one long-lived worker thread."""

    def __init__(self, opener: Opener | None = None) -> None:
        self._opener = opener or open_live
        self._queue: queue.Queue[tuple[Callable[[Live], Any], bool, Future]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._live: Live | None = None
        # Plain copies for other threads (which window is ours).
        self.pid = 0
        self.program = ""

    def call(self, fn: Callable[[Live], Any], *, launch: bool = True,
             timeout: float = CALL_TIMEOUT_SECONDS) -> Any:
        """Run ``fn(live)`` on the browser thread and return its result (or raise its error).

        launch=False: never start the browser; raise NotRunning if it is not open.
        """
        if threading.current_thread() is self._thread:
            return fn(self._ensure(launch))
        self._start_worker()
        done: Future = Future()
        self._queue.put((fn, launch, done))
        try:
            return done.result(timeout)
        except FutureTimeout:
            raise TimeoutError(
                f"The browser did not answer within {timeout:g}s. It may be busy or frozen: "
                "tell the user, or try again."
            ) from None

    def _start_worker(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="agent-browser")
                self._thread.start()

    def _run(self) -> None:
        while True:
            fn, launch, done = self._queue.get()
            if not done.set_running_or_notify_cancel():
                continue
            try:
                done.set_result(fn(self._ensure(launch)))
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                done.set_exception(exc)

    def _ensure(self, launch: bool) -> Live:
        if self._live is not None and self._live.connected:
            return self._live
        self._live = self._opener(launch, self._live)
        self.pid, self.program = self._live.pid, self._live.info.program
        return self._live

    def known_pid(self) -> tuple[int, str]:
        """(process id, program) of the agent's browser, also before the first connect of
        this run (read from the session file). (0, "") when unknown."""
        if self.pid:
            return self.pid, self.program
        try:
            info = default_browser()
        except BrowserError:
            return 0, ""
        saved = read_session(paths_for(info)[1]) or {}
        pid = saved.get("pid")
        return (pid, info.program) if isinstance(pid, int) and pid > 0 else (0, "")


# ---- opening (worker thread; Windows and network calls are thin) ------------------

def default_browser() -> BrowserInfo:
    return check_browser(exe_from_command(_read_default_command()))


def open_live(launch: bool, old: Live | None) -> Live:  # pragma: no cover - real browser
    """Connect to the agent's browser left open earlier, or start it (when `launch`)."""
    info = default_browser()
    profile, session_file = paths_for(info)
    saved = read_session(session_file)
    reachable = saved is not None and _port_answers(saved["port"])
    if not reachable and not launch:  # checked before starting Playwright: list_windows asks often
        raise NotRunning("The agent's browser is not open. Open a page with web_open first.")
    pw = old.playwright if old is not None else _start_playwright()
    if saved is not None and reachable:
        browser = _connect(pw, saved["port"])
        pid = int(saved.get("pid") or 0)
    else:
        port = _free_port()
        pid = _launch(info.exe, profile, port)
        if not _wait_for_port(port, START_TIMEOUT_SECONDS):
            raise BrowserError(
                f"The agent's browser ({Path(info.exe).stem}) did not open its control port within "
                f"{START_TIMEOUT_SECONDS:g}s. If an agent browser window is still open from before, ask "
                "the user to close it, then try again."
            )
        write_session(session_file, {"port": port, "pid": pid, "exe": info.exe})
        browser = _connect(pw, port)
    if not browser.contexts:
        raise BrowserError("Connected to the browser, but it has no window. Try again.")
    _deny_downloads(browser)
    return Live(pw, browser, browser.contexts[0], info, pid)


def _read_default_command() -> str:  # pragma: no cover - needs Windows
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_CHOICE_KEY) as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as key:
            return str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return ""


def _start_playwright() -> Any:  # pragma: no cover - real browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserError("The browser tools need Playwright: pip install playwright") from None
    return sync_playwright().start()


def _connect(pw: Any, port: int) -> Any:  # pragma: no cover - real browser
    return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)


def _deny_downloads(browser: Any) -> None:  # pragma: no cover - real browser
    """The agent never downloads files (they could be anything)."""
    try:
        browser.new_browser_cdp_session().send("Browser.setDownloadBehavior", {"behavior": "deny"})
    except Exception:  # noqa: BLE001 - an older browser may not support it; not fatal
        pass


def _free_port() -> int:  # pragma: no cover - real network
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_answers(port: int) -> bool:  # pragma: no cover - real network
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def _wait_for_port(port: int, timeout: float) -> bool:  # pragma: no cover - real network
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_answers(port):
            return True
        time.sleep(0.25)
    return False


def _launch(exe: str, profile: Path, port: int) -> int:  # pragma: no cover - starts a program
    """Start the browser detached, so it stays open when the app closes."""
    profile.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile}", *LAUNCH_ARGS, "about:blank"]
    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:  # also leave the app's job object, so closing a console does not close it
        proc = subprocess.Popen(args, creationflags=detached | 0x01000000, close_fds=True, **io)
    except OSError:
        proc = subprocess.Popen(args, creationflags=detached, close_fds=True, **io)
    return proc.pid


SESSION = BrowserSession()
