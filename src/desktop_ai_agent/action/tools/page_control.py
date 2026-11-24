"""Act on web pages in the agent's own browser (LLM path).

The model uses the same tools as on the desktop; ui_control and window_control send
the web cases here:
  list_elements(area="page")        -> numbered page elements (accessibility snapshot)
  click / type_text / move_mouse    -> act on those numbers inside the page (no real mouse)
  browser_read                      -> the page's text, in parts, or around a word
  browser_go                        -> back / forward / reload
  list_windows / window_control     -> also list, switch to and close the agent's tabs

Rules kept in code, not left to the model:
  - The user's OWN browser is in front -> the page's address is handed over to the
    agent's browser automatically (owner's decision); the user's browser is never driven.
  - A cloud model + a Google work site (Gmail, Classroom, Docs, Drive) -> nothing is
    read until the user said "proceed"; the OK lasts until New chat (``reset``).
  - Cookie / consent buttons are clicked only after the user answered.
  - Never type into password fields; Enter only in search fields.
  - The only JavaScript run in pages is the fixed snippets below; text from the model
    is only ever typed as text.

Page lists (owner's test on YouTube, 2026-11-24: the profile menu sat at the END of the
page and was cut off at 60):
  - an element keeps its number while it stays on the page (numbers follow Playwright's
    element reference), so a number the model saw earlier still means the same thing;
  - order: what appeared after the last click or typing (the menu it opened) first, and
    it stays first in later lists until the next click or typing; then matches for the
    request, then what is on screen, then what is scrolled away;
  - right after a click or typing, the list shows ONLY what changed (much shorter);
    the whole list comes when the page navigated, nothing new appeared, or the model
    lists again.

Everything Playwright-side runs on the browser thread through ``_call``; only plain
data comes back here.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from urllib.parse import urlparse

from desktop_ai_agent.action.tools import browser_session
from desktop_ai_agent.action.tools.web_open import check_url
from desktop_ai_agent.input.element_ranking import scores
from desktop_ai_agent.input.page_elements import (
    PageElement,
    clean_name,
    find_snippets,
    format_page_list,
    page_elements,
    parse_snapshot,
    short,
    split_parts,
)

MAX_LISTED = 60
PART_CHARS = 1500  # about 375 tokens per browser_read part
ACTION_TIMEOUT_MS = 5000
SNAPSHOT_TIMEOUT_MS = 10000
GOTO_TIMEOUT_MS = browser_session.GOTO_TIMEOUT_MS
SETTLE_LOAD_MS = 5000
SETTLE_IDLE_MS = 1500
CLOSE_WAIT_MS = 2000
HIGHLIGHT_MS = 3000
GO_ACTIONS = ("back", "forward", "reload")

PRIVATE_HOSTS = ("mail.google.com", "classroom.google.com", "docs.google.com", "drive.google.com")
# Window programs that are web browsers (the user's own ones get handed over).
BROWSER_PROGRAMS = frozenset({
    "chrome", "msedge", "opera", "brave", "vivaldi", "chromium", "thorium", "arc", "yandex",
    "firefox", "waterfox", "librewolf", "floorp", "iexplore",
})

_consented: set[str] = set()  # hosts the user said "proceed" for, until New chat
_pending: set[str] = set()  # hosts the user was just asked about


@dataclass
class _TabMemory:
    """What the last list of one tab looked like, so the next one can number and diff."""

    address: str  # the URL without its #fragment; another address = another page
    ids: dict[str, int] = field(default_factory=dict)  # element reference -> its number
    next_id: int = 1
    last_refs: frozenset[str] = frozenset()  # references in the previous list
    fresh: frozenset[str] = frozenset()  # appeared after the last click/typing: listed first
    changed_by: str = ""  # "click" / "typing" since the previous list, else ""


_memory: dict[int, _TabMemory] = {}  # tab key -> memory
_tab_problem = ""  # why the last tabs() call could not read the tabs ("" = fine)


def reset() -> None:
    """Forget the cloud OKs and the page numbering (New chat)."""
    _consented.clear()
    _pending.clear()
    _memory.clear()


# ---- thin seams (the tests replace these) -----------------------------------------

def _session() -> Any:
    return browser_session.SESSION


def _windows() -> list[Any]:  # pragma: no cover - needs Windows
    from desktop_ai_agent.input.windows import list_open_windows

    return list_open_windows(own_pid=os.getpid())


def _address_of(hwnd: int) -> str | None:  # pragma: no cover - needs Windows
    from desktop_ai_agent.input.ui_elements import address_bar_url

    return address_bar_url(hwnd)


def _bring_to_front(hwnd: int) -> bool:  # pragma: no cover - needs Windows
    from desktop_ai_agent.input.windows import bring_to_front

    return bring_to_front(hwnd)


# ---- pure rules --------------------------------------------------------------------

def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").casefold()


def page_address(url: str) -> str:
    """The URL without its #fragment (Gmail and others route inside one page with it)."""
    return (url or "").split("#", 1)[0]


def same_address(a: str, b: str) -> bool:
    """Do two URLs show the same page? Scheme, 'www.', a trailing '/' and #fragment ignored."""
    def norm(url: str) -> tuple[str, str, str]:
        parts = urlparse(url or "")
        host = (parts.hostname or "").casefold()
        host = host[4:] if host.startswith("www.") else host
        return host, parts.path.rstrip("/"), parts.query

    return bool(urlparse(a or "").hostname) and norm(a) == norm(b)


def is_private(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in PRIVATE_HOSTS)


_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|sure|proceed|go ahead|continue|do it|fine|accept|agree|allow)\b", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|do not|stop|cancel|reject|decline|deny|refuse)\b", re.I)
_NOT_A_NO = re.compile(r"\bno (problem|worries)\b", re.I)


def says_yes(text: str) -> bool:
    text = _NOT_A_NO.sub(" ", text or "")
    return bool(_YES.search(text)) and not _NO.search(text)


def says_no(text: str) -> bool:
    return bool(_NO.search(_NOT_A_NO.sub(" ", text or "")))


_CONSENT_REJECT = re.compile(r"^(reject|decline|deny|refuse)\b|\breject (all|cookies)\b", re.I)
_CONSENT_ACCEPT = re.compile(r"^(accept|allow|agree|i agree|i accept)\b|\b(accept|allow) (all|cookies)\b", re.I)


def consent_choice(name: str) -> str | None:
    """'accept' / 'reject' for a cookie or consent button ("Accept all", "Reject all"), else None."""
    text = clean_name(name)
    if _CONSENT_REJECT.search(text):
        return "reject"
    if _CONSENT_ACCEPT.search(text):
        return "accept"
    return None


def check_consent_click(name: str, request: str) -> None:
    """Cookie / consent buttons are pressed only after the user answered the question."""
    choice = consent_choice(name)
    if choice is None:
        return
    answered = says_yes(request) if choice == "accept" else says_no(request)
    if not answered:
        raise PermissionError(
            f"'{name}' answers the site's cookie or consent question. Ask the user whether to accept "
            "or reject; click it only after they answer."
        )


def check_cloud(url: str, cloud: bool, request: str) -> None:
    """Refuse to read a Google work site for a cloud model until the user said proceed."""
    host = host_of(url)
    if not cloud or not is_private(host) or host in _consented:
        return
    if host in _pending and says_yes(request):
        _pending.discard(host)
        _consented.add(host)
        return
    _pending.add(host)
    raise PermissionError(
        f"{host} holds private content (email, school work) and the selected AI model runs in the "
        "cloud, so this page would be sent there. Ask the user whether to proceed; call this again "
        "only after they agree."
    )


# ---- which window is a browser ----------------------------------------------------

def agent_windows() -> list[Any]:
    pid, _ = _session().known_pid()
    return [w for w in _windows() if pid and w.pid == pid]


def browser_kind(hwnd: int | None) -> str | None:
    """'agent' (our browser), 'user' (the user's own browser) or None (not a browser)."""
    if not hwnd:
        return None
    win = next((w for w in _windows() if w.hwnd == hwnd), None)
    if win is None:
        return None
    pid, _ = _session().known_pid()
    if pid and win.pid == pid:
        return "agent"
    return "user" if win.program.casefold() in BROWSER_PROGRAMS else None


def _front_window() -> None:
    for win in agent_windows()[:1]:
        _bring_to_front(win.hwnd)


# ---- browser-thread helpers --------------------------------------------------------

def _brief(exc: BaseException) -> str:
    """First line of a Playwright error (its call logs would flood the model's context)."""
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]


def _is_timeout(exc: BaseException) -> bool:
    return type(exc).__name__ == "TimeoutError"


def _call(fn: Callable[[Any], Any], *, launch: bool = True) -> Any:
    def guarded(live: Any) -> Any:
        try:
            return fn(live)
        except Exception as exc:
            if type(exc).__module__.startswith("playwright"):
                raise RuntimeError(f"Browser: {_brief(exc)}") from None
            raise

    return _session().call(guarded, launch=launch)


def _current(live: Any) -> Any:
    page = live.page()
    if page is None:
        raise LookupError("No page is open in the agent's browser. Open one with web_open first.")
    return page


def _page_of(live: Any, el: PageElement) -> Any:
    page = live.page_by_key(el.page_key)
    if page is None:
        raise LookupError(f"The tab of element {el.id} ('{el.name}') was closed. Call list_elements again.")
    live.set_current(page)
    return page


def _locate(page: Any, el: PageElement) -> Any:
    loc = page.locator(f"aria-ref={el.ref}")
    try:
        count = loc.count()
    except Exception:  # noqa: BLE001 - a stale ref may raise instead of counting 0
        count = 0
    if count != 1:
        raise LookupError(
            f"Element {el.id} ('{el.name}') is no longer on the page (it changed). "
            "Call list_elements again, then use the new number."
        )
    return loc


def _settle(page: Any) -> None:
    """Give a click time to load what it opened; a slow page is fine (the list shows it)."""
    for state, ms in (("domcontentloaded", SETTLE_LOAD_MS), ("networkidle", SETTLE_IDLE_MS)):
        try:
            page.wait_for_load_state(state, timeout=ms)
        except Exception:  # noqa: BLE001 - still loading or busy: not an error
            pass


def _act(el: PageElement, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as exc:
        if _is_timeout(exc):
            raise LookupError(
                f"Element {el.id} ('{el.name}') did not respond: it may be hidden or covered (for example "
                "by a popup). Call list_elements again."
            ) from None
        raise


# ---- hand-over ---------------------------------------------------------------------

class HandOverFailed(LookupError):
    """The user's browser shows no web page that could be handed over (e.g. a new tab)."""


def hand_over(hwnd: int) -> str | None:
    """Open the page shown in the user's own browser window in the agent's browser.

    Returns a note for the model, or None when no web address could be read there.
    A tab that already shows the same address is reused.
    """
    url = _address_of(hwnd)
    if not url:
        return None
    url = check_url(url)

    def work(live: Any) -> str:
        same = next((p for p in live.pages() if p.url.rstrip("/") == url.rstrip("/")), None)
        page = same
        if page is None:
            page, _loaded = live.new_tab(url)
        live.set_current(page)
        page.bring_to_front()
        return page.title()

    title = _call(work)
    _front_window()
    return (
        f"The page was open in the user's own browser, so it was opened in the agent's browser "
        f"('{short(clean_name(title), 60)}')."
    )


# ---- reading -----------------------------------------------------------------------

def _number(found: list[PageElement], key: int, url: str) -> tuple[list[PageElement], set[str], _TabMemory, bool]:
    """Give every element its lasting number. Returns (elements, references that are new
    since the previous list, the tab's memory, whether there was a previous list)."""
    memory = _memory.get(key)
    if memory is None or memory.address != page_address(url):
        memory = _memory[key] = _TabMemory(page_address(url))  # another page: start again
    had_previous = bool(memory.last_refs)
    numbered: list[PageElement] = []
    for el in found:
        number = memory.ids.get(el.ref)
        if number is None:
            number = memory.ids[el.ref] = memory.next_id
            memory.next_id += 1
        numbered.append(replace(el, id=number))
    new = {el.ref for el in numbered if el.ref not in memory.last_refs} if had_previous else set()
    return numbered, new, memory, had_previous


def order_elements(elements: list[PageElement], request: str, new: set[str]) -> list[PageElement]:
    """New since the last list first, then request matches, then on screen, then page order."""
    totals = scores(request, (el.name for el in elements))
    rank = sorted(
        range(len(elements)),
        key=lambda i: (elements[i].ref not in new, -totals[i], bool(elements[i].where), i),
    )
    return [elements[i] for i in rank]


def list_page(
    request: str = "", cloud: bool = False, handover_hwnd: int | None = None, *, strict: bool = False
) -> tuple[list[PageElement], dict[str, Any]]:
    """(every element of the page, result for the model) for the agent's current tab.

    Every element is returned (not only the shown ones) so that a number the model saw
    in an earlier list stays usable while that element is still on the page.
    With `handover_hwnd` (the user's own browser window) its page is opened in the
    agent's browser first; `strict` raises HandOverFailed when that window shows no web
    address, instead of listing whatever the agent's browser shows.
    """
    notes = [hand_over(handover_hwnd)] if handover_hwnd else []
    if handover_hwnd and strict and not notes[0]:
        raise HandOverFailed("The browser in front shows no web page.")
    check_cloud(_call(lambda live: _current(live).url), cloud, request)

    def work(live: Any) -> tuple[str, str, int, str, Any]:
        page = _current(live)
        snapshot = page.aria_snapshot(mode="ai", boxes=True, timeout=SNAPSHOT_TIMEOUT_MS)
        height = page.evaluate("() => window.innerHeight")
        return page.title(), page.url, live.key(page), snapshot, height

    title, url, key, snapshot, height = _call(work)
    found = page_elements(parse_snapshot(snapshot), float(height) if height else None, key)
    numbered, new, memory, had_previous = _number(found, key, url)
    changed_by, memory.changed_by = memory.changed_by, ""
    current = frozenset(el.ref for el in numbered)
    # A menu a click opened stays first while it is open, until the next click/typing.
    memory.fresh = frozenset(new) if changed_by else (memory.fresh & current) | new
    memory.last_refs = current
    changes_only = bool(changed_by and had_previous and new)
    pool = [el for el in numbered if el.ref in new] if changes_only else numbered
    shown = order_elements(pool, request, set(memory.fresh))[:MAX_LISTED]
    result: dict[str, Any] = {
        "area": "page",
        "page": short(clean_name(title), 80),
        "site": host_of(url),
        "count": len(shown),
        "elements": format_page_list(shown),
    }
    if changes_only:
        notes.append(
            f"Only what appeared after your {changed_by} is shown ({len(pool)} new). The other "
            f"{len(numbered) - len(pool)} elements keep their numbers; call list_elements with area page "
            "to see them all."
        )
    elif changed_by and had_previous:
        notes.append(f"Nothing new appeared after your {changed_by}: this is the whole page.")
    if not shown:
        notes.append("No named elements on this page yet. It may still be loading: wait 2 s and list "
                     "again, or call browser_read.")
    elif len(shown) < len(pool):
        notes.append(f"Showing {len(shown)} of {len(pool)} elements: new ones and matches for the request "
                     "come first, then what is on screen. '(below)' ones are further down: clicking scrolls "
                     "to them.")
    if any(notes):
        result["note"] = " ".join(n for n in notes if n)
    return numbered, result


def _changed(key: int, by: str) -> None:
    """Remember that `by` ("click" / "typing") may have changed this tab: the next list
    then shows only what is new."""
    memory = _memory.get(key)
    if memory is not None:
        memory.changed_by = by


def read(
    find: str | None = None,
    part: int = 1,
    request: str = "",
    cloud: bool = False,
    handover_hwnd: int | None = None,
) -> dict[str, Any]:
    """The current tab's text: one part of it, or the pieces around `find`."""
    notes = [hand_over(handover_hwnd)] if handover_hwnd else []
    check_cloud(_call(lambda live: _current(live).url), cloud, request)

    def work(live: Any) -> tuple[str, str, str]:
        page = _current(live)
        return page.title(), page.url, page.inner_text("body", timeout=SNAPSHOT_TIMEOUT_MS)

    title, url, text = _call(work)
    result: dict[str, Any] = {"page": short(clean_name(title), 80), "site": host_of(url)}
    if find:
        hits = find_snippets(text, find)
        result.update(find=find, found=len(hits), text="\n".join(hits))
        if not hits:
            notes.append(f"'{find}' does not appear in the page text.")
    else:
        parts = split_parts(text, PART_CHARS) or [""]
        number = min(max(int(part), 1), len(parts))
        result.update(part=number, parts=len(parts), text=parts[number - 1])
        if number < len(parts):
            notes.append(f"More text: call browser_read with part={number + 1}.")
    if any(notes):
        result["note"] = " ".join(n for n in notes if n)
    return result


# ---- acting ------------------------------------------------------------------------

def click(el: PageElement, button: str = "left", double: bool = False) -> dict[str, Any]:
    def work(live: Any) -> tuple[str, str, bool]:
        page = _page_of(live, el)
        loc = _locate(page, el)
        before = live.pages()

        def press() -> None:
            loc.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            if double:
                loc.dblclick(button=button, timeout=ACTION_TIMEOUT_MS)
            else:
                loc.click(button=button, timeout=ACTION_TIMEOUT_MS)

        _act(el, press)
        new = live.follow_new(before)
        target = new or page
        _settle(target)
        return target.title(), target.url, new is not None, live.key(target)

    title, url, new_tab, key = _call(work)
    _changed(key, "click")
    result: dict[str, Any] = {
        "clicked": el.name, "type": el.type, "page": short(clean_name(title), 80), "site": host_of(url),
    }
    if new_tab:
        result["note"] = "It opened in a new tab; the agent now works in that tab."
    return result


_FIELD_JS = """el => {
  const hint = [el.getAttribute('aria-label'), el.getAttribute('placeholder'), el.name, el.id,
                el.getAttribute('role')].join(' ');
  return {
    password: el.type === 'password',
    kind: el.tagName === 'INPUT' ? 'input' : el.tagName === 'TEXTAREA' ? 'textarea' : 'editable',
    empty: !String(el.value || el.textContent || '').trim(),
    search: el.type === 'search' || /search/i.test(hint) || !!el.closest('[role=search], search'),
  };
}"""


def type_into(el: PageElement, text: str, submit: bool = False) -> dict[str, Any]:
    """Enter `text` into a page field. One-line inputs (and empty or search text areas)
    are replaced; other text areas and rich editors get the text added at the end."""

    def work(live: Any) -> tuple[str, str]:
        page = _page_of(live, el)
        loc = _locate(page, el)
        info = loc.evaluate(_FIELD_JS) or {}
        if info.get("password"):
            raise PermissionError("That is a password field. Never type passwords: ask the user to do it.")
        search = el.type == "SearchBox" or bool(info.get("search"))
        if submit and not search:
            raise ValueError(
                f"submit (Enter) works only in search fields, and '{el.name}' is not one. Call type_text "
                "without submit and tell the user the text is ready to send."
            )
        kind = info.get("kind")

        def enter_text() -> None:
            loc.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            if kind == "input" or (kind == "textarea" and (search or info.get("empty"))):
                loc.fill(text, timeout=ACTION_TIMEOUT_MS)
            else:
                loc.click(timeout=ACTION_TIMEOUT_MS)
                page.keyboard.press("Control+End")
                page.keyboard.insert_text(text)

        _act(el, enter_text)
        target = page
        if submit:
            before = live.pages()
            _act(el, lambda: loc.press("Enter", timeout=ACTION_TIMEOUT_MS))
            target = live.follow_new(before) or page
            _settle(target)
        return target.title(), target.url, live.key(target)

    title, url, key = _call(work)
    _changed(key, "typing")
    return {
        "typed_into": el.name,
        "type": el.type,
        "characters": len(text),
        "page": short(clean_name(title), 80),
        "note": "Enter was pressed: the search ran." if submit
        else "Text entered; Enter was not pressed. Tell the user the text is ready to send.",
    }


_HIGHLIGHT_JS = """(el, ms) => {
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const outline = el.style.outline, offset = el.style.outlineOffset;
  el.style.outline = '3px solid #ff2d55';
  el.style.outlineOffset = '2px';
  setTimeout(() => { el.style.outline = outline; el.style.outlineOffset = offset; }, ms);
}"""


def highlight(el: PageElement) -> dict[str, Any]:
    """move_mouse on a web page: scroll to the element and outline it for a moment."""

    def work(live: Any) -> None:
        page = _page_of(live, el)
        loc = _locate(page, el)
        _act(el, lambda: loc.evaluate(_HIGHLIGHT_JS, HIGHLIGHT_MS))
        page.bring_to_front()

    _call(work)
    _front_window()
    return {"highlighted": el.name, "type": el.type, "note": "It is outlined in red on the page for 3 s."}


def go(action: str) -> dict[str, Any]:
    """Back, forward or reload in the current tab."""
    if action not in GO_ACTIONS:
        raise ValueError(f"action must be one of {list(GO_ACTIONS)}, got '{action}'")

    def work(live: Any) -> tuple[str, str, bool]:
        page = _current(live)
        before = page.url
        step = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action]
        try:
            step(wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        except Exception as exc:
            if not _is_timeout(exc):
                raise
        moved = action == "reload" or page.url != before
        if moved:
            _settle(page)
        return page.title(), page.url, moved, live.key(page)

    title, url, moved, key = _call(work)
    if moved:
        _memory.pop(key, None)  # another page (or a rebuilt one): number it afresh
    result: dict[str, Any] = {"action": action, "page": short(clean_name(title), 80), "site": host_of(url)}
    if not moved:
        result["note"] = f"There is no page to go {action} to."
    return result


def open_url(url: str) -> dict[str, Any]:
    """web_open: the page in a new tab of the agent's browser, which comes to the front."""
    raw = check_url(url)

    def work(live: Any) -> tuple[str, bool, bool]:
        same = next((p for p in live.pages() if same_address(p.url, raw)), None)
        if same is not None:
            live.set_current(same)
            same.bring_to_front()
            return same.title(), True, True
        page, loaded = live.new_tab(raw)
        page.bring_to_front()
        return page.title(), loaded, False

    title, loaded, reused = _call(work)
    _front_window()
    result: dict[str, Any] = {"opened": raw, "page": short(clean_name(title), 80)}
    if reused:
        result["note"] = "It was already open: switched to that tab."
    elif not loaded:
        result["note"] = "The page is still loading."
    return result


# ---- tabs (for list_windows / window_control) -------------------------------------

@dataclass(frozen=True)
class TabInfo:
    key: int
    title: str
    url: str
    active: bool = False
    hwnd: int = 0  # the agent browser's window
    pid: int = 0

    @property
    def program(self) -> str:
        """The site, so window_control's name matching works on tabs too ('youtube')."""
        return host_of(self.url)


def tab_problem() -> str:
    """Why the last tabs() call could not read the tabs ("" when it could, or the browser is closed)."""
    return _tab_problem


def tabs() -> list[TabInfo]:
    """The agent browser's tabs, or [] when it is not open (it is never started for this)."""
    global _tab_problem

    def work(live: Any) -> list[tuple[int, str, str, bool]]:
        current = live.page()
        rows = []
        for p in live.pages():
            try:
                title = p.title()
            except Exception:  # noqa: BLE001 - one unreadable tab must not hide the others
                title = ""
            rows.append((live.key(p), title, p.url, p is current))
        return rows

    try:
        rows = _call(work, launch=False)
    except browser_session.NotRunning:
        _tab_problem = ""
        return []
    except (browser_session.BrowserError, TimeoutError, RuntimeError) as exc:
        _tab_problem = str(exc)[:200] or type(exc).__name__
        return []
    _tab_problem = ""
    win = next(iter(agent_windows()), None)
    hwnd, pid = (win.hwnd, win.pid) if win is not None else (0, 0)
    return [TabInfo(k, clean_name(t) or u, u, a, hwnd, pid) for k, t, u, a in rows]


def focus_tab(tab: TabInfo) -> dict[str, Any]:
    def work(live: Any) -> str:
        page = live.page_by_key(tab.key)
        if page is None:
            raise LookupError(f"The tab '{tab.title}' is closed. Call list_windows again.")
        page.bring_to_front()
        live.set_current(page)
        return page.title()

    title = _call(work, launch=False)
    _front_window()
    return {"tab": short(clean_name(title), 80), "in_front": True}


def close_tab(tab: TabInfo) -> dict[str, Any]:
    """Close a tab like its X button. A page that asks "Leave site?" keeps its tab open."""
    def work(live: Any) -> bool:
        page = live.page_by_key(tab.key)
        if page is None:
            return True  # already gone
        page.close(run_before_unload=True)
        if page.is_closed():
            return True
        try:
            page.wait_for_event("close", timeout=CLOSE_WAIT_MS)
        except Exception:  # noqa: BLE001 - timeout (still open) or already closed
            pass
        return bool(page.is_closed())

    closed = _call(work, launch=False)
    result: dict[str, Any] = {"tab": tab.title, "closed": closed}
    if not closed:
        result["note"] = (
            "The tab is still open: the page asks whether to leave it (unsaved changes?). Do not choose "
            "for the user: tell them, and let them close it themselves if they want to."
        )
    return result
