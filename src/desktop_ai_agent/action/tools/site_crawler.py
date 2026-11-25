"""Explore one site in the agent's browser, read-only, for its site map.

Owner's decisions (2026-11-25): mapping runs only when the user asks for it, at most
40 pages, menus are opened, and Stop keeps what was mapped so far (marked partial).

How it explores
  - in a SECOND tab of the agent's browser (same logins); the tab closes at the end and
    the user's tab is left where it was;
  - starting at the page in front, it opens same-site links, the ones in menus,
    navigation, headers and sidebars first, then the rest of the page, at most
    MAX_DEPTH clicks deep;
  - it clicks buttons that announce a menu (aria-haspopup, or aria-expanded=false),
    each one once per site (headers repeat on every page), records what appeared,
    then presses Escape. Nothing inside a menu is clicked;
  - it never types, submits or toggles anything, never opens links that could change
    something (log out, delete...), files or other sites, and skips buttons with risky
    names (ui_control's list).

Everything Playwright-side runs on the browser thread through ``_call``; only plain
data comes back. The tests replace ``_session`` with a fake site.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from desktop_ai_agent.action.tools import browser_session
from desktop_ai_agent.action.tools.ui_control import risky_word
from desktop_ai_agent.input.element_ranking import fold
from desktop_ai_agent.input.page_elements import CLICKABLE_TYPE, parse_snapshot
from desktop_ai_agent.input.site_pages import (
    PageItem,
    content_patterns,
    is_content,
    page_items,
    page_key,
    site_host,
    why_not_follow,
)

MAX_PAGES = 40
MAX_DEPTH = 3
MAX_ITEMS = 3000  # ~40 s of embedding on the CPU
MAX_SECONDS = 300.0  # a slow site still ends, as a partial map
MAX_MENUS_PER_PAGE = 6
MAX_POPUP_CHECKS = 40  # buttons checked for a menu per page (new names only)
GOTO_TIMEOUT_MS = browser_session.GOTO_TIMEOUT_MS
IDLE_MS = 2000
SNAPSHOT_TIMEOUT_MS = 10000
MENU_CLICK_MS = 3000
POPUP_CHECK_MS = 1000  # a vanished element must not wait Playwright's default 30 s
MENU_WAIT_MS = 700
PATH_SEP = " › "
OPENER_TYPES = frozenset({"Button", CLICKABLE_TYPE})
# Items whose link address the snapshot may not show: it prints /url only for role
# "link", so <a role=menuitem href=...> (a menu entry) arrives without its address.
HREF_TYPES = frozenset({"MenuItem", CLICKABLE_TYPE, "TabItem", "TreeItem", "Button"})
MAX_HREF_CHECKS = 80

# Does this element announce a menu or a panel it opens? (fixed snippet, no model text)
POPUP_JS = """el => {
  const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
  return (popup !== '' && popup !== 'false') || el.getAttribute('aria-expanded') === 'false';
}"""

# The address of the link this element is, or sits in, or holds ('' when none).
HREF_JS = """el => {
  const a = el.closest('a[href]') || el.querySelector('a[href]');
  return a ? a.href : '';
}"""


class PageFailed(RuntimeError):
    """One page could not be read (a Playwright error); the crawl goes on without it."""


@dataclass(frozen=True)
class CrawledPage:
    url: str
    title: str
    via: str = ""  # how to get there from the start page: "Settings › Privacy" ("" = start)


@dataclass(frozen=True)
class CrawledItem:
    label: str
    kind: str
    page: int  # index into CrawlResult.pages
    area: str = "other"  # "nav" | "main" | "other" | "menu"
    menu: str = ""  # the button that opens the menu it is in
    url: str = ""  # where a link goes


@dataclass
class CrawlResult:
    host: str
    start_url: str
    pages: list[CrawledPage] = field(default_factory=list)
    items: list[CrawledItem] = field(default_factory=list)
    partial: str = ""  # "" = complete, else why it ended early: "stopped" | "time limit"
    failed: int = 0  # pages that could not be read


# ---- browser-thread steps ----------------------------------------------------------

def _session() -> Any:
    return browser_session.SESSION


def _brief(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]


def _call(fn: Callable[[Any], Any]) -> Any:
    def guarded(live: Any) -> Any:
        try:
            return fn(live)
        except Exception as exc:
            if type(exc).__module__.startswith("playwright"):
                raise PageFailed(_brief(exc)) from None
            raise

    return _session().call(guarded)


def _is_timeout(exc: BaseException) -> bool:
    return type(exc).__name__ == "TimeoutError"


def _open_tab(live: Any) -> tuple[int, str]:
    """(key of a new tab for exploring, address of the user's page it starts from)."""
    user_page = live.page()
    if user_page is None:
        raise LookupError("No page is open in the agent's browser. Open the site with web_open first.")
    live.set_current(user_page)  # pinned: the new tab must never become the agent's tab
    start = user_page.url
    tab = live.context.new_page()
    return live.key(tab), start


def _tab(live: Any, key: int) -> Any:
    tab = live.page_by_key(key)
    if tab is None:
        raise LookupError("The tab used for mapping was closed.")
    return tab


def _missing_hrefs(tab: Any, snapshot: str, base_url: str) -> dict[str, str]:
    """ref -> link address for the items whose address the snapshot left out."""
    refs = [
        it.ref for it in page_items(parse_snapshot(snapshot), base_url)
        if not it.url and it.kind in HREF_TYPES
    ][:MAX_HREF_CHECKS]
    found: dict[str, str] = {}
    for ref in refs:
        try:
            href = tab.locator(f"aria-ref={ref}").evaluate(HREF_JS, timeout=POPUP_CHECK_MS)
        except Exception:  # noqa: BLE001 - gone or not an element: no address
            continue
        if isinstance(href, str) and href.startswith(("http://", "https://")):
            found[ref] = href
    return found


def _visit(live: Any, key: int, url: str) -> tuple[str, str, str, dict[str, str]]:
    """Load `url` in the mapping tab: (address it ended on, title, snapshot, hrefs)."""
    tab = _tab(live, key)
    try:
        tab.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    except Exception as exc:
        if not _is_timeout(exc):
            raise
    try:
        tab.wait_for_load_state("networkidle", timeout=IDLE_MS)
    except Exception:  # noqa: BLE001 - a busy page is fine: read what is there
        pass
    snapshot = tab.aria_snapshot(mode="ai", boxes=True, timeout=SNAPSHOT_TIMEOUT_MS)
    return tab.url, tab.title(), snapshot, _missing_hrefs(tab, snapshot, tab.url)


def _popup_flags(live: Any, key: int, refs: list[str]) -> list[bool]:
    tab = _tab(live, key)
    flags = []
    for ref in refs:
        try:
            flags.append(bool(tab.locator(f"aria-ref={ref}").evaluate(POPUP_JS, timeout=POPUP_CHECK_MS)))
        except Exception:  # noqa: BLE001 - gone or not an element: no menu
            flags.append(False)
    return flags


def _open_menu(live: Any, key: int, ref: str, url: str) -> tuple[str, str, dict[str, str]]:
    """Click a menu button: ('', snapshot with the menu open, hrefs), or (address, '', {})
    when the click went to another page (the tab is sent back to `url`)."""
    tab = _tab(live, key)
    before = {id(p) for p in live.pages()}
    tab.locator(f"aria-ref={ref}").click(timeout=MENU_CLICK_MS)
    tab.wait_for_timeout(MENU_WAIT_MS)
    for page in live.pages():  # a button that opened a new tab: close it again
        if id(page) not in before:
            page.close()
    if page_key(tab.url) != page_key(url):
        moved = tab.url
        tab.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        return moved, "", {}
    snapshot = tab.aria_snapshot(mode="ai", boxes=True, timeout=SNAPSHOT_TIMEOUT_MS)
    hrefs = _missing_hrefs(tab, snapshot, url)  # while the menu is still open
    tab.keyboard.press("Escape")
    return "", snapshot, hrefs


def _close_tab(live: Any, key: int) -> None:
    tab = live.page_by_key(key)
    if tab is not None:
        tab.close()
    user_page = live.page()
    if user_page is not None:
        user_page.bring_to_front()


# ---- the crawl (calling thread) ----------------------------------------------------

@dataclass
class _Crawl:
    host: str
    should_stop: Callable[[], bool]
    max_pages: int
    deadline: float
    clock: Callable[[], float]
    result: CrawlResult
    queue: list[tuple[int, int, int, str, str]] = field(default_factory=list)
    seen_pages: set[str] = field(default_factory=set)  # page keys visited or queued
    seen_items: set[tuple[str, ...]] = field(default_factory=set)
    popup_checked: set[str] = field(default_factory=set)  # button names checked for a menu, once per site
    order: int = 0

    def push(self, url: str, via: str, depth: int, priority: int) -> None:
        key = page_key(url)
        if key in self.seen_pages or depth > MAX_DEPTH or why_not_follow(url, self.host):
            return
        self.seen_pages.add(key)
        self.order += 1
        heapq.heappush(self.queue, (priority, depth, self.order, url, via))

    def add_item(self, item: PageItem, page: int, area: str, menu: str = "") -> None:
        if len(self.result.items) >= MAX_ITEMS:
            return
        label = fold(item.label)
        if item.url:
            key: tuple[str, ...] = ("link", label, page_key(item.url))
        elif area == "main":
            key = ("main", label, item.kind, str(page))
        else:
            key = ("shared", label, item.kind, menu)
        if key in self.seen_items:
            return
        self.seen_items.add(key)
        self.result.items.append(CrawledItem(item.label, item.kind, page, area, menu, item.url))

    def stop_reason(self) -> str:
        if self.should_stop():
            return "stopped"
        if self.clock() >= self.deadline:
            return "time limit"
        return ""


def _priority(area: str) -> int:
    return {"menu": 0, "nav": 0, "other": 1}.get(area, 2)


def _path(via: str, *steps: str) -> str:
    return PATH_SEP.join(s for s in (via, *steps) if s)


def _with_hrefs(items: list[PageItem], hrefs: dict[str, str]) -> list[PageItem]:
    return [replace(it, url=hrefs[it.ref]) if not it.url and it.ref in hrefs else it for it in items]


def _record_page(
    crawl: _Crawl, url: str, title: str, snapshot: str, hrefs: dict[str, str], via: str, depth: int
) -> tuple[int, list[PageItem]]:
    index = len(crawl.result.pages)
    crawl.result.pages.append(CrawledPage(url, title, via))
    items = _with_hrefs(page_items(parse_snapshot(snapshot), url), hrefs)
    patterns = content_patterns(items)
    kept = [it for it in items if not is_content(it, patterns)]
    for it in kept:
        crawl.add_item(it, index, it.area)
        if it.url:
            crawl.push(it.url, _path(via, it.label), depth + 1, _priority(it.area))
    return index, kept


def _explore_menus(crawl: _Crawl, tab_key: int, url: str, index: int, items: list[PageItem], via: str, depth: int) -> None:
    """Open the page's menu buttons that were not opened yet on this site."""
    candidates = [
        it for it in items
        if it.kind in OPENER_TYPES and fold(it.label) not in crawl.popup_checked and not risky_word(it.label)
    ][:MAX_POPUP_CHECKS]
    if not candidates:
        return
    crawl.popup_checked.update(fold(it.label) for it in candidates)
    flags = _call(lambda live: _popup_flags(live, tab_key, [it.ref for it in candidates]))
    openers = [it for it, flag in zip(candidates, flags) if flag][:MAX_MENUS_PER_PAGE]
    before = {it.ref for it in items}
    for opener in openers:
        if crawl.stop_reason():
            return
        try:
            moved, snapshot, hrefs = _call(lambda live, ref=opener.ref: _open_menu(live, tab_key, ref, url))
        except (PageFailed, LookupError, TimeoutError):
            continue
        if moved:  # the button is a link in disguise: map that page like a link
            crawl.push(moved, _path(via, opener.label), depth + 1, 0)
            continue
        shown = _with_hrefs(page_items(parse_snapshot(snapshot), url), hrefs)
        patterns = content_patterns(shown)
        for it in shown:
            if it.ref in before or is_content(it, patterns) or it.kind == "Heading":
                continue
            crawl.add_item(it, index, "menu", opener.label)
            if it.url:
                crawl.push(it.url, _path(via, opener.label, it.label), depth + 1, 0)
        before |= {it.ref for it in shown}  # a menu that stayed open is not the next one's


def crawl_site(
    should_stop: Callable[[], bool] | None = None,
    *,
    max_pages: int = MAX_PAGES,
    max_seconds: float = MAX_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> CrawlResult:
    """Map the site of the agent's current tab. Ends early (partial) on Stop or the time limit."""
    tab_key, start = _call(_open_tab)
    host = site_host(start)
    if not host or not start.startswith(("http://", "https://")):
        _call(lambda live: _close_tab(live, tab_key))
        raise LookupError("The page in front is not a web site. Open the site with web_open first.")
    crawl = _Crawl(
        host, should_stop or (lambda: False), max_pages, clock() + max_seconds, clock,
        CrawlResult(host, start),
    )
    crawl.seen_pages.add(page_key(start))
    heapq.heappush(crawl.queue, (0, 0, 0, start, ""))
    try:
        while crawl.queue and len(crawl.result.pages) < crawl.max_pages:
            reason = crawl.stop_reason()
            if reason:
                crawl.result.partial = reason
                break
            _priority_, depth, _order, url, via = heapq.heappop(crawl.queue)
            try:
                final, title, snapshot, hrefs = _call(lambda live, u=url: _visit(live, tab_key, u))
            except (PageFailed, TimeoutError):
                crawl.result.failed += 1
                continue
            if site_host(final) != host:  # sent to a login page or another site
                crawl.result.failed += 1
                continue
            final_key = page_key(final)
            if final_key != page_key(url) and final_key in crawl.seen_pages and crawl.result.pages:
                continue  # redirected to a page that is already mapped or queued
            crawl.seen_pages.add(final_key)
            index, items = _record_page(crawl, final, title, snapshot, hrefs, via, depth)
            _explore_menus(crawl, tab_key, final, index, items, via, depth)
    finally:
        try:
            _call(lambda live: _close_tab(live, tab_key))
        except Exception:  # noqa: BLE001 - the map is still worth keeping
            pass
    return crawl.result
