"""Measure page element lists for the browser tool (step 1; a dev script the owner runs).

For each Edge launch mode and test site it prints and saves:
  - three element lists, ranked by the site's sample request and cut to 60 lines:
      UIA    today's list_elements("active") on the Edge window
      ARIA   Playwright page.locator("body").aria_snapshot()
      AXTREE CDP Accessibility.getFullAXTree (main frame only)
  - found / shown / characters / estimated tokens / seconds for each list,
  - the page text size and its first 1500 characters,
  - the load time and, for Gmail and Classroom, whether the Google login worked.

Launch modes (each gets its own profile folder, emptied when the mode starts):
  a  Playwright launch_persistent_context (msedge, no network port, no --enable-automation)
  b  msedge.exe started normally with a debug port on 127.0.0.1, then connect_over_cdp

Usage (project root):
  python scripts/web_spike.py [--mode a|b|both] [--sites youtube,github,gmail,classroom]

Needs `pip install "playwright>=1.49"` (aria_snapshot). It uses the installed Edge, so
`playwright install` is not needed. The report goes to
logs/web_spike/<YYYYmmdd_HHMMSS>/report.txt and contains Gmail and Classroom content:
check it before sharing. No passwords, cookies, screenshots or HTML are read or saved.

The pure helpers (aria-line parsing, role mapping, formatting, token estimate) import
nothing from Playwright; step 3 moves them to input/page_elements.py with tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, NamedTuple
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.sync_api import Page, Playwright

ROOT = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    """Put src/ on sys.path, the way app/__main__.py does."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


_bootstrap()

from desktop_ai_agent.core.monitors import enable_dpi_awareness  # noqa: E402
from desktop_ai_agent.input.element_ranking import rank_by_request  # noqa: E402

MAX_LISTED = 60  # same cut as action/tools/ui_control.MAX_LISTED
MAX_NAME = 60  # same name cut as input/ui_elements.format_for_model
CHARS_PER_TOKEN = 4  # same rough estimate as orchestrator/agent_loop.py
TEXT_SAMPLE_CHARS = 1500
GOTO_TIMEOUT_MS = 60_000
IDLE_TIMEOUT_MS = 10_000
AFTER_LOGIN_TIMEOUT_MS = 15_000
UIA_SETTLE_SECONDS = 0.5
DEBUG_PORT_WAIT_SECONDS = 20.0
MAX_ERROR_CHARS = 300

METHODS = ("UIA", "ARIA", "AXTREE")
INTERACTIVE_ROLES = frozenset({
    "link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio",
    "tab", "menuitem", "option", "switch", "slider",
})
ROLE_TYPES = {
    "link": "Hyperlink",
    "button": "Button",
    "textbox": "Edit",
    "searchbox": "Edit",
    "combobox": "ComboBox",
    "checkbox": "CheckBox",
    "radio": "RadioButton",
    "tab": "TabItem",
    "menuitem": "MenuItem",
    "option": "ListItem",
}

LOGIN_PROMPT = "Log in to Google in the Edge window, then press Enter here"
GOOGLE_SIGN_IN_HOST = "accounts.google.com"
BLOCK_TEXT = "this browser or app may not be secure"
BLOCK_PATH = "/signin/rejected"  # Google's "Couldn't sign you in" page

PROFILE_ROOT = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "desktop_ai_agent" / "spike"
)
EDGE_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
EDGE_FALLBACKS = tuple(
    Path(os.environ.get(var) or default) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    for var, default in (("ProgramFiles(x86)", r"C:\Program Files (x86)"), ("ProgramFiles", r"C:\Program Files"))
)

MODE_LABELS = {
    "a": "Playwright launch_persistent_context (msedge, no network port, without --enable-automation)",
    "b": "msedge.exe started normally with --remote-debugging-port on 127.0.0.1, then connect_over_cdp",
}

WARNING = (
    "WARNING: this report contains Gmail and Classroom content (email subjects, names). "
    "Check it before you share it."
)


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    url: str
    host: str  # the host that means "we are on the site" (signed in, for Google sites)
    request: str
    login: bool = False


SITES = (
    Site("youtube", "YouTube", "https://www.youtube.com", "www.youtube.com", "search for lofi music"),
    Site("github", "GitHub", "https://github.com", "github.com", "open the explore page"),
    Site("gmail", "Gmail", "https://mail.google.com", "mail.google.com", "open the newest email", login=True),
    Site("classroom", "Classroom", "https://classroom.google.com", "classroom.google.com",
         "open the newest assignment", login=True),
)


# ---- results ----------------------------------------------------------------------

class ListOutput(NamedTuple):
    text: str
    shown: int
    found: int  # before the MAX_LISTED cut


@dataclass
class MethodResult:
    name: str
    found: int = 0
    shown: int = 0
    text: str = ""
    seconds: float = 0.0
    error: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class TextResult:
    chars: int = 0
    seconds: float = 0.0
    sample: str = ""
    error: str = ""

    @property
    def tokens(self) -> int:
        return self.chars // CHARS_PER_TOKEN


@dataclass
class SiteResult:
    mode: str
    site: Site
    error: str = ""
    final_host: str = ""
    dom_seconds: float | None = None  # goto until domcontentloaded
    load_seconds: float | None = None  # including the networkidle wait
    idle_timeout: bool = False
    login_prompted: bool = False
    reached: bool | None = None
    blocked: bool | None = None
    login_answer: str = ""  # "y" / "n" / "?" (no console input); "" = not asked
    methods: list[MethodResult] = field(default_factory=list)
    text: TextResult | None = None


@dataclass(frozen=True)
class PageElement:
    id: int  # 1-based, in page order (before ranking), like UIA scan ids
    type: str
    name: str


# ---- pure helpers (no Playwright; step 3 moves these to input/page_elements.py) ----

def clean_name(value: Any) -> str:
    """Collapse whitespace and drop invisible format characters, as ui_elements does."""
    text = "".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def short_name(text: str, limit: int = MAX_NAME) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def collapse_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def type_for_role(role: str) -> str:
    """ARIA role -> our element type name ('link' -> 'Hyperlink'; unknown roles capitalized)."""
    return ROLE_TYPES.get(role) or role[:1].upper() + role[1:]


_ARIA_KEY = re.compile(
    r'^(?P<role>[a-z][a-z-]*)'
    r'(?:\s+(?:"(?P<name>(?:[^"\\]|\\.)*)"|/(?P<regex>(?:[^/\\]|\\.)*)/))?'
)


def _yaml_key(item: str) -> str:
    """Unwrap a YAML single-quoted key ('button "a: b"' -> button "a: b"; '' -> ')."""
    if not item.startswith("'"):
        return item
    out: list[str] = []
    i = 1
    while i < len(item):
        if item[i] == "'":
            if item[i + 1 : i + 2] != "'":
                break
            i += 1
        out.append(item[i])
        i += 1
    return "".join(out)


def _unescape(raw: str) -> str:
    try:
        value = json.loads(f'"{raw}"')
    except ValueError:
        return raw
    return value if isinstance(value, str) else raw


def parse_aria_line(line: str) -> tuple[str, str] | None:
    """'  - button "Search" [pressed]:' -> ('button', 'Search'); None if not a role line.

    Lines may end with ':' or carry [attributes]. A role without a name gives ''.
    """
    item = line.strip()
    if not item.startswith("- "):
        return None
    match = _ARIA_KEY.match(_yaml_key(item[2:].lstrip()))
    if match is None:
        return None
    if match.group("name") is not None:
        name = _unescape(match.group("name"))
    else:
        name = match.group("regex") or ""
    return match.group("role"), name


def aria_pairs(snapshot: str) -> list[tuple[str, str]]:
    """(role, name) for every role line of an aria snapshot, in page order."""
    return [pair for pair in map(parse_aria_line, (snapshot or "").splitlines()) if pair is not None]


def _ax_value(prop: Any) -> str:
    value = prop.get("value") if isinstance(prop, dict) else None
    return value if isinstance(value, str) else ""


def axtree_pairs(nodes: Iterable[Any]) -> list[tuple[str, str]]:
    """(role, name) for every node of Accessibility.getFullAXTree that is not ignored."""
    return [
        (_ax_value(node.get("role")), _ax_value(node.get("name")))
        for node in nodes
        if isinstance(node, dict) and not node.get("ignored")
    ]


def to_elements(pairs: Iterable[tuple[str, str]]) -> list[PageElement]:
    """Keep named interactive roles, map them to our types, drop exact (type, name) duplicates."""
    seen: set[tuple[str, str]] = set()
    found: list[PageElement] = []
    for role, raw_name in pairs:
        name = clean_name(raw_name)
        if role not in INTERACTIVE_ROLES or not name:
            continue
        key = (type_for_role(role), name)
        if key in seen:
            continue
        seen.add(key)
        found.append(PageElement(len(found) + 1, key[0], name))
    return found


def format_elements(elements: Iterable[PageElement]) -> str:
    """One line per element, e.g. '17: Button "Search"' (style of ui_elements.format_for_model)."""
    return "\n".join(f'{el.id}: {el.type} "{short_name(el.name)}"' for el in elements)


def page_list(pairs: Iterable[tuple[str, str]], request: str) -> ListOutput:
    """Rank by the request BEFORE the cut, as list_elements does, then format."""
    elements = to_elements(pairs)
    shown = rank_by_request(elements, request, lambda el: el.name)[:MAX_LISTED]
    return ListOutput(format_elements(shown), len(shown), len(elements))


_SHOWING = re.compile(r"Showing \d+ of (\d+) elements")


def uia_found(note: Any, count: int) -> int:
    """Elements before the cut: list_elements says 'Showing X of Y' only when it cut."""
    match = _SHOWING.search(str(note or ""))
    return int(match.group(1)) if match else count


def _fold(text: str) -> str:
    return clean_name(text).casefold()


def find_edge_window(windows: Iterable[Any], page_title: str, pid: int | None) -> int | None:
    """hwnd of our Edge window: by process id first, else an msedge window showing the page title."""
    listed = list(windows)
    if pid:
        for win in listed:
            if win.pid == pid:
                return win.hwnd
    title = _fold(page_title)
    if title:
        for win in listed:
            if win.program.casefold() == "msedge" and title in _fold(win.title):
                return win.hwnd
    return None


def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def is_google_sign_in(url: str) -> bool:
    return host_of(url) == GOOGLE_SIGN_IN_HOST


def shows_block(url: str, body_text: str) -> bool:
    """Google's 'This browser or app may not be secure' page (English text, or its URL)."""
    return BLOCK_PATH in urlparse(url or "").path or BLOCK_TEXT in (body_text or "").casefold()


def pick_sites(text: str) -> list[Site]:
    keys = [part.strip().lower() for part in (text or "").split(",") if part.strip()]
    known = {site.key for site in SITES}
    unknown = [key for key in keys if key not in known]
    if unknown or not keys:
        raise ValueError(f"unknown or missing site(s): {', '.join(unknown) or '(none)'}; "
                         f"choose from {', '.join(site.key for site in SITES)}")
    return [site for site in SITES if site.key in keys]  # fixed order: Gmail's login helps Classroom


def describe_error(exc: BaseException) -> str:
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {first}"[:MAX_ERROR_CHARS]


def _yes_no(value: bool | None) -> str:
    return "?" if value is None else ("yes" if value else "no")


# ---- report rendering (pure) --------------------------------------------------------

def render_header(when: datetime, playwright_version: str, dpi: str, modes: list[str], sites: list[Site]) -> str:
    lines = [
        f"Web spike report, {when:%Y-%m-%d %H:%M:%S}",
        f"Playwright {playwright_version}, Python {sys.version.split()[0]}, DPI awareness: {dpi}",
        f"Modes: {', '.join(modes)} | Sites: {', '.join(site.label for site in sites)}",
        *(f"Mode {mode}: {MODE_LABELS[mode]}" for mode in modes),
        "Each mode starts from an empty profile folder of its own.",
        f"Lists: ranked by the site's sample request, cut to {MAX_LISTED}, names cut to {MAX_NAME} characters.",
        f"Tokens = characters // {CHARS_PER_TOKEN}. UIA seconds cover the list_elements call only "
        f"(not bringing Edge to the front or the {UIA_SETTLE_SECONDS} s wait).",
        "AXTREE covers the main frame only. Element numbers are page order before ranking.",
    ]
    return "\n".join(lines)


def render_method(m: MethodResult) -> list[str]:
    if m.error:
        return [f"[{m.name}] ERROR after {m.seconds:.2f} s: {m.error}"]
    head = f"[{m.name}] found {m.found}, shown {m.shown}, {m.chars} chars, ~{m.tokens} tokens, {m.seconds:.2f} s"
    return [head, m.text or "(empty list)"]


def render_text(t: TextResult) -> list[str]:
    if t.error:
        return [f"[Page text] ERROR after {t.seconds:.2f} s: {t.error}"]
    return [
        f"[Page text] {t.chars} chars, ~{t.tokens} tokens, {t.seconds:.2f} s",
        f"First {TEXT_SAMPLE_CHARS} characters:",
        t.sample or "(empty)",
    ]


def render_site(r: SiteResult) -> str:
    s = r.site
    lines = [f"--- Mode {r.mode} / {s.label} ({s.url}) ---", f'Request: "{s.request}"']
    if r.dom_seconds is not None and r.load_seconds is not None:
        load = (f"Load: {r.dom_seconds:.2f} s to domcontentloaded, "
                f"{r.load_seconds:.2f} s including the networkidle wait")
        if r.idle_timeout:
            load += f" (networkidle timed out after {IDLE_TIMEOUT_MS // 1000} s)"
        if r.login_prompted:
            load += " [measured on the Google sign-in page]"
        lines.append(load)
    if r.final_host:
        lines.append(f"Final host: {r.final_host}")
    if s.login and r.reached is not None:
        lines.append(
            f"Login: sign-in prompt shown: {_yes_no(r.login_prompted)}, site reached: {_yes_no(r.reached)}, "
            f"'may not be secure' block: {_yes_no(r.blocked)}, your answer: {r.login_answer or 'not asked'}"
        )
    if r.error:
        lines.append(f"ERROR: {r.error}")
    for m in r.methods:
        lines += ["", *render_method(m)]
    if r.text is not None:
        lines += ["", *render_text(r.text)]
    return "\n".join(lines) + "\n"


def format_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows)


def _method_cell(m: MethodResult | None) -> str:
    if m is None:
        return "-"
    return "ERR" if m.error else f"{m.found}/{m.shown}/{m.tokens}/{m.seconds:.1f}"


def _text_cell(t: TextResult | None) -> str:
    if t is None:
        return "-"
    return "ERR" if t.error else str(t.tokens)


def _load_cell(r: SiteResult) -> str:
    if r.dom_seconds is None:
        return "-"
    return f"{r.dom_seconds:.1f}" + ("*" if r.idle_timeout else "") + (" (sign-in)" if r.login_prompted else "")


def _login_cell(r: SiteResult) -> str:
    if not r.site.login or r.reached is None:
        return "-"
    yn = {True: "y", False: "n", None: "?"}
    return f"R:{yn[r.reached]} B:{yn[r.blocked]} L:{r.login_answer or '-'}"


def summary_row(r: SiteResult) -> list[str]:
    by_name = {m.name: m for m in r.methods}
    return [
        r.mode,
        r.site.key,
        *(_method_cell(by_name.get(name)) for name in METHODS),
        _text_cell(r.text),
        _load_cell(r),
        _login_cell(r),
    ]


def render_summary(results: list[SiteResult]) -> str:
    header = ["mode", "site", *(f"{name} f/s/tok/sec" for name in METHODS), "text tok", "load s", "login"]
    return "\n".join([
        "=== Summary ===",
        format_table([header, *(summary_row(r) for r in results)]),
        "",
        f"f/s/tok/sec = found before the {MAX_LISTED} cut / shown / estimated tokens / seconds; "
        "ERR = failed, - = not measured (see the site's section above).",
        f"load s = seconds to domcontentloaded; * = networkidle timed out after {IDLE_TIMEOUT_MS // 1000} s; "
        "(sign-in) = measured on the Google sign-in page.",
        "login: R = site reached, B = 'may not be secure' block seen, "
        "L = your answer to 'Did the login work?' (- = not asked).",
    ])


# ---- console and report file ----------------------------------------------------------

class Report:
    """Prints each part and appends it to report.txt at once, so a stopped run keeps what it measured."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        self.path = path

    def add(self, text: str) -> None:
        print(text, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def safe_console() -> None:
    """Page names may hold characters the console code page lacks: replace them instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def wait_for_enter(prompt: str) -> None:
    try:
        input(prompt + " ")
    except EOFError:
        pass


def ask_yes_no(question: str) -> str:
    while True:
        try:
            answer = input(question).strip().lower()
        except EOFError:
            return "?"
        if answer[:1] in ("y", "n"):
            return answer[:1]
        print("Please answer y or n.")


# ---- Playwright and Windows (thin) ----------------------------------------------------

def load_playwright() -> Callable[[], Any] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


def playwright_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("playwright")
    except PackageNotFoundError:
        return "unknown"


def fresh_profile(path: Path) -> Path:
    """Empty the mode's own profile folder, so a login from an earlier run does not count."""
    if PROFILE_ROOT not in path.parents:
        raise ValueError(f"refusing to empty a folder outside the spike folder: {path.name}")
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise RuntimeError(
                f"could not empty the profile folder '{path.name}' ({type(exc).__name__}); "
                "close any Edge window left from an earlier run"
            ) from exc
    path.mkdir(parents=True)
    return path


def registry_edge_path() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, EDGE_APP_PATHS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except (ImportError, OSError):
        return ""
    return str(value or "").strip().strip('"')


def find_edge_exe() -> Path:
    for candidate in (registry_edge_path(), *EDGE_FALLBACKS):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("msedge.exe not found (registry App Paths key and Program Files)")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_debug_port(port: int, proc: subprocess.Popen[Any]) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + DEBUG_PORT_WAIT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Edge exited (code {proc.returncode}) before opening the debug port; "
                "is an Edge from an earlier run still using this profile?"
            )
        try:
            with opener.open(url, timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise TimeoutError(f"Edge did not open debug port {port} within {DEBUG_PORT_WAIT_SECONDS:.0f} s")


def stop_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@contextmanager
def launch_mode_a(pw: Playwright, profile: Path) -> Iterator[tuple[Page, int | None]]:
    context = pw.chromium.launch_persistent_context(
        str(profile),
        channel="msedge",
        headless=False,
        no_viewport=True,
        ignore_default_args=["--enable-automation"],
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        yield page, None  # a persistent context does not expose the browser's process id
    finally:
        try:
            context.close()
        except Exception as exc:  # noqa: BLE001 - e.g. the window was closed by hand
            print(f"(closing mode a: {describe_error(exc)})")


@contextmanager
def launch_mode_b(pw: Playwright, profile: Path) -> Iterator[tuple[Page, int | None]]:
    exe = find_edge_exe()
    port = free_port()
    proc = subprocess.Popen(
        [str(exe), f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    browser = None
    try:
        wait_for_debug_port(port, proc)
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if not browser.contexts:
            raise RuntimeError("Edge offered no browser context over CDP")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        yield page, proc.pid
    finally:
        if browser is not None:
            try:
                browser.close()  # for a CDP connection this disconnects
            except Exception as exc:  # noqa: BLE001
                print(f"(disconnecting mode b: {describe_error(exc)})")
        stop_process(proc)


LAUNCHERS = {"a": launch_mode_a, "b": launch_mode_b}


def settle(page: Page, timeout_ms: int) -> bool:
    """Wait for domcontentloaded, then networkidle (IDLE_TIMEOUT_MS). False if networkidle timed out."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return False
    return True


def open_site(page: Page, site: Site, result: SiteResult) -> None:
    start = time.perf_counter()
    page.goto(site.url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    result.dom_seconds = time.perf_counter() - start
    result.idle_timeout = not settle(page, GOTO_TIMEOUT_MS)
    result.load_seconds = time.perf_counter() - start


def body_text_or_empty(page: Page) -> str:
    try:
        return page.inner_text("body", timeout=10_000)
    except Exception:  # noqa: BLE001 - only used for the block check
        return ""


def check_login(page: Page, site: Site, result: SiteResult) -> None:
    """Pause for a manual Google login when needed. Never reads, stores or types a password."""
    if is_google_sign_in(page.url):
        result.login_prompted = True
        wait_for_enter(LOGIN_PROMPT)
        settle(page, AFTER_LOGIN_TIMEOUT_MS)
    result.reached = host_of(page.url) == site.host
    result.blocked = shows_block(page.url, body_text_or_empty(page))
    if result.login_prompted:
        result.login_answer = ask_yes_no("Did the login work? [y/n] ")


def focus_edge(page: Page, pid: int | None) -> None:
    from desktop_ai_agent.input import windows

    hwnd = find_edge_window(windows.list_open_windows(own_pid=os.getpid()), page.title(), pid)
    if hwnd is None:
        raise LookupError("no Edge window found (by process id, or msedge with the page title)")
    if not windows.bring_to_front(hwnd):
        raise RuntimeError("could not bring the Edge window to the front")


def uia_list(request: str) -> ListOutput:
    from desktop_ai_agent.action.tools import ui_control

    result = ui_control.list_elements("active", request)
    count = int(result.get("count") or 0)
    return ListOutput(str(result.get("elements") or ""), count, uia_found(result.get("note"), count))


def aria_snapshot(page: Page) -> str:
    return page.locator("body").aria_snapshot()


def ax_nodes(page: Page) -> list[Any]:
    session = page.context.new_cdp_session(page)
    try:
        return list(session.send("Accessibility.getFullAXTree").get("nodes") or [])
    finally:
        session.detach()


def measure(name: str, run: Callable[[], ListOutput]) -> MethodResult:
    start = time.perf_counter()
    try:
        out = run()
    except Exception as exc:  # noqa: BLE001 - a failed method must not stop the others
        return MethodResult(name, seconds=time.perf_counter() - start, error=describe_error(exc))
    return MethodResult(name, out.found, out.shown, out.text, time.perf_counter() - start)


def measure_uia(page: Page, pid: int | None, request: str) -> MethodResult:
    try:
        focus_edge(page, pid)
    except Exception as exc:  # noqa: BLE001
        return MethodResult("UIA", error=describe_error(exc))
    time.sleep(UIA_SETTLE_SECONDS)
    return measure("UIA", lambda: uia_list(request))


def measure_text(page: Page) -> TextResult:
    start = time.perf_counter()
    try:
        text = collapse_whitespace(page.inner_text("body"))
    except Exception as exc:  # noqa: BLE001
        return TextResult(seconds=time.perf_counter() - start, error=describe_error(exc))
    return TextResult(len(text), time.perf_counter() - start, text[:TEXT_SAMPLE_CHARS])


# ---- run ---------------------------------------------------------------------------

def run_site(page: Page, pid: int | None, mode: str, site: Site) -> SiteResult:
    result = SiteResult(mode, site)
    try:
        open_site(page, site, result)
        if site.login:
            check_login(page, site, result)
        result.final_host = host_of(page.url)
    except Exception as exc:  # noqa: BLE001 - a failed site must not stop the run
        result.error = describe_error(exc)
        return result
    result.methods = [
        measure_uia(page, pid, site.request),
        measure("ARIA", lambda: page_list(aria_pairs(aria_snapshot(page)), site.request)),
        measure("AXTREE", lambda: page_list(axtree_pairs(ax_nodes(page)), site.request)),
    ]
    result.text = measure_text(page)
    return result


def run_mode(pw: Playwright, mode: str, sites: list[Site], report: Report, results: list[SiteResult]) -> None:
    report.add(f"\n=== Mode {mode}: {MODE_LABELS[mode]} ===\n")
    done = 0
    try:
        profile = fresh_profile(PROFILE_ROOT / f"profile_{mode}")
        print(f"[mode {mode}] profile folder: {profile}")
        with LAUNCHERS[mode](pw, profile) as (page, pid):
            for site in sites:
                print(f"[mode {mode}] opening {site.label} ...", flush=True)
                result = run_site(page, pid, mode, site)
                results.append(result)
                done += 1
                report.add(render_site(result))
    except Exception as exc:  # noqa: BLE001 - the other mode still runs
        error = describe_error(exc)
        report.add(f"Mode {mode} failed: {error}\n")
        results.extend(SiteResult(mode, site, error=f"mode {mode} failed: {error}") for site in sites[done:])


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure page element lists for the browser tool (step 1).")
    parser.add_argument("--mode", choices=("a", "b", "both"), default="both", help="Edge launch mode (default: both)")
    parser.add_argument("--sites", default=",".join(site.key for site in SITES),
                        help="comma-separated: youtube,github,gmail,classroom (default: all)")
    args = parser.parse_args(argv)
    try:
        args.site_list = pick_sites(args.sites)
    except ValueError as exc:
        parser.error(str(exc))
    args.mode_list = ["a", "b"] if args.mode == "both" else [args.mode]
    return args


def main(argv: list[str] | None = None) -> int:
    dpi = enable_dpi_awareness()
    safe_console()
    args = parse_args(argv)
    sync_playwright = load_playwright()
    if sync_playwright is None:
        print("Playwright is not installed. Install it (version 1.49 or newer) with:")
        print('    pip install "playwright>=1.49"')
        print("Edge is used as installed: 'playwright install' is not needed.")
        return 1

    started = datetime.now()
    report = Report(ROOT / "logs" / "web_spike" / f"{started:%Y%m%d_%H%M%S}" / "report.txt")
    report.add(render_header(started, playwright_version(), dpi, args.mode_list, args.site_list))
    results: list[SiteResult] = []
    try:
        with sync_playwright() as pw:
            for mode in args.mode_list:
                run_mode(pw, mode, args.site_list, report, results)
    except KeyboardInterrupt:
        report.add("\nStopped with Ctrl+C: the summary covers the sites measured so far.")
    report.add("\n" + render_summary(results))
    report.add("\n" + WARNING)
    print(f"\nReport saved to {report.path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
