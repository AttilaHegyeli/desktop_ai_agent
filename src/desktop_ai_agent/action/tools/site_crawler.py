"""Explore one site in the agent's browser, read-only, for its site map.

Owner's decisions (2026-11-25): mapping runs only when the user asks for it, at most
40 pages, menus are opened, Stop keeps what was mapped so far (marked partial), and
the map should hold the part of the site one can USE, not its legal text.

How it explores
  - in a SECOND tab of the agent's browser (same logins); the tab closes at the end and
    the user's tab is left where it was;
  - starting at the page in front, nearest pages first: at each depth, links from
    menus first, then navigation (header, sidebar), then the rest. Pages reached through
    a menu (the account menu -> Settings) hand that first place on to their own links,
    so a settings page's sub-pages come early. At most MAX_DEPTH clicks deep;
  - a page whose header and sidebar differ from the start page's is outside the app
    (YouTube's legal and marketing pages): it is not mapped and its links are not
    followed. Once two links of one group (a footer) led outside, the rest of that
    group is skipped;
  - it clicks buttons that announce a menu (aria-haspopup, or aria-expanded=false),
    each one once per site (headers repeat on every page), records what appeared,
    then presses Escape. Nothing inside a menu is clicked;
  - it never types, submits or toggles anything, never opens links that could change
    something (log out, delete...), files or other sites, and skips buttons with risky
    names (ui_control's list) or risky page code (page_control.code_risk).

Speed (the owner's YouTube run hit the 5-minute limit at 28 pages, ~11 s a page): one
script call per page reads every link address and menu flag (was ~120 calls), and the
wait for a quiet network is 1 s (YouTube is never quiet). Each page's time is kept.

Everything Playwright-side runs on the browser thread through ``_call``; only plain
data comes back. The tests replace ``_session`` with a fake site.
"""
from __future__ import annotations

import heapq
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from desktop_ai_agent.action.tools import browser_session
from desktop_ai_agent.action.tools.page_control import code_risk
from desktop_ai_agent.action.tools.ui_control import risky_word
from desktop_ai_agent.input.element_ranking import fold
from desktop_ai_agent.input.page_elements import CLICKABLE_TYPE, parse_snapshot
from desktop_ai_agent.input.site_pages import (
    ELEMENT_FACTS_JS,
    HEADING_KIND,
    PageItem,
    clean_title,
    content_patterns,
    drop_repeated_rows,
    facts_by_box,
    inside_app,
    is_content,
    page_items,
    page_key,
    shell_names,
    site_host,
    why_not_follow,
    with_facts,
)

MAX_PAGES = 40
MAX_DEPTH = 3
MAX_ITEMS = 3000  # ~40 s of embedding on the CPU
MAX_SECONDS = 300.0  # a slow site still ends, as a partial map
MAX_MENUS_PER_PAGE = 6
OUTSIDE_PER_GROUP = 2  # links of one group that led outside the app before the rest is skipped
GOTO_TIMEOUT_MS = browser_session.GOTO_TIMEOUT_MS
IDLE_MS = 1000
SNAPSHOT_TIMEOUT_MS = 10000
MENU_CLICK_MS = 3000
MENU_WAIT_MS = 700
PATH_SEP = " › "
OPENER_TYPES = frozenset({"Button", CLICKABLE_TYPE})
AREA_PRIORITY = {"menu": 0, "nav": 1, "other": 2, "main": 3}
START_PRIORITY = 1


class PageFailed(RuntimeError):
    """One page could not be read (a Playwright error); the crawl goes on without it."""


@dataclass(frozen=True)
class CrawledPage:
    url: str
    title: str
    via: str = ""  # how to get there from the start page: "Settings › Privacy" ("" = start)
    seconds: float = 0.0  # time spent on it (loading, reading, menus)


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
    outside: int = 0  # pages left out: outside the app (legal, marketing) or another site
    skipped: int = 0  # links not opened: the rest of a group whose links led outside


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


def _read(tab: Any) -> tuple[str, list[Any]]:
    """(snapshot, ELEMENT_FACTS_JS rows) of the tab as it is now."""
    snapshot = tab.aria_snapshot(mode="ai", boxes=True, timeout=SNAPSHOT_TIMEOUT_MS)
    try:
        rows = tab.evaluate(ELEMENT_FACTS_JS)
    except Exception:  # noqa: BLE001 - the snapshot alone still maps the page
        rows = []
    return snapshot, rows if isinstance(rows, list) else []


def _visit(live: Any, key: int, url: str) -> tuple[str, str, str, list[Any]]:
    """Load `url` in the mapping tab: (address it ended on, title, snapshot, facts)."""
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
    snapshot, rows = _read(tab)
    return tab.url, tab.title(), snapshot, rows


def _open_menu(live: Any, key: int, ref: str, url: str) -> tuple[str, str, list[Any]]:
    """Click a menu button: ('', snapshot with the menu open, facts), or (address, '', [])
    when the click went to another page (the tab is sent back to `url`)."""
    tab = _tab(live, key)
    before = {id(p) for p in live.pages()}
    opener = tab.locator(f"aria-ref={ref}")
    if code_risk(opener):  # its page code says send / delete / buy...: never clicked
        return "", "", []
    opener.click(timeout=MENU_CLICK_MS)
    tab.wait_for_timeout(MENU_WAIT_MS)
    for page in live.pages():  # a button that opened a new tab: close it again
        if id(page) not in before:
            page.close()
    if page_key(tab.url) != page_key(url):
        moved = tab.url
        tab.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        return moved, "", []
    snapshot, rows = _read(tab)  # while the menu is still open
    tab.keyboard.press("Escape")
    return "", snapshot, rows


def _close_tab(live: Any, key: int) -> None:
    tab = live.page_by_key(key)
    if tab is not None:
        tab.close()
    user_page = live.page()
    if user_page is not None:
        user_page.bring_to_front()


# ---- the crawl (calling thread) ----------------------------------------------------

@dataclass(order=True)
class _Link:
    depth: int
    priority: int
    order: int
    url: str = field(compare=False)
    via: str = field(compare=False)
    group: tuple[int, int] = field(compare=False, default=(-1, -1))  # (source page, container)


@dataclass
class _Crawl:
    host: str
    should_stop: Callable[[], bool]
    max_pages: int
    deadline: float
    clock: Callable[[], float]
    result: CrawlResult
    queue: list[_Link] = field(default_factory=list)
    seen_pages: set[str] = field(default_factory=set)  # page keys visited or queued
    seen_items: set[tuple[str, ...]] = field(default_factory=set)
    popup_checked: set[str] = field(default_factory=set)  # menu button names, once per site
    shell: frozenset[str] | None = None  # the start page's header/sidebar names
    outside_groups: Counter[tuple[int, int]] = field(default_factory=Counter)
    order: int = 0

    def push(self, url: str, via: str, depth: int, priority: int, group: tuple[int, int] = (-1, -1)) -> None:
        key = page_key(url)
        if key in self.seen_pages or depth > MAX_DEPTH or why_not_follow(url, self.host):
            return
        self.seen_pages.add(key)
        self.order += 1
        heapq.heappush(self.queue, _Link(depth, priority, self.order, url, via, group))

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


def _child_priority(area: str, parent_priority: int) -> int:
    """A page reached through a menu hands its first place on to its links."""
    return 0 if parent_priority == 0 else AREA_PRIORITY.get(area, 3)


def _path(via: str, *steps: str) -> str:
    return PATH_SEP.join(s for s in (via, *steps) if s)


def _items(snapshot: str, rows: list[Any], url: str) -> list[PageItem]:
    return with_facts(page_items(parse_snapshot(snapshot), url), facts_by_box(rows))


def _record_page(crawl: _Crawl, link: _Link, url: str, title: str, items: list[PageItem]) -> tuple[int, list[PageItem]]:
    index = len(crawl.result.pages)
    crawl.result.pages.append(CrawledPage(url, clean_title(title), link.via))
    patterns = content_patterns(items)
    kept = drop_repeated_rows([it for it in items if not is_content(it, patterns)])
    for it in kept:
        crawl.add_item(it, index, it.area)
        if it.url:
            crawl.push(
                it.url, _path(link.via, it.label), link.depth + 1,
                _child_priority(it.area, link.priority), (index, it.group),
            )
    return index, kept


def _explore_menus(crawl: _Crawl, tab_key: int, url: str, index: int, items: list[PageItem], link: _Link) -> None:
    """Open the page's menu buttons that were not opened yet on this site (header and
    sidebar ones first)."""
    openers = [
        it for it in items
        if it.popup and it.kind in OPENER_TYPES and fold(it.label) not in crawl.popup_checked
        and not risky_word(it.label)
    ]
    openers.sort(key=lambda it: it.area == "main")  # stable: page order otherwise
    before = {it.ref for it in items}
    for opener in openers[:MAX_MENUS_PER_PAGE]:
        if crawl.stop_reason():
            return
        crawl.popup_checked.add(fold(opener.label))
        try:
            moved, snapshot, rows = _call(lambda live, ref=opener.ref: _open_menu(live, tab_key, ref, url))
        except (PageFailed, LookupError, TimeoutError):
            continue
        if moved:  # the button is a link in disguise: map that page like a link
            crawl.push(moved, _path(link.via, opener.label), link.depth + 1, 0)
            continue
        shown = _items(snapshot, rows, url)
        patterns = content_patterns(shown)
        for it in drop_repeated_rows(shown):
            if it.ref in before or is_content(it, patterns) or it.kind == HEADING_KIND:
                continue
            crawl.add_item(it, index, "menu", opener.label)
            if it.url:
                crawl.push(it.url, _path(link.via, opener.label, it.label), link.depth + 1, 0)
        before |= {it.ref for it in shown}  # a menu that stayed open is not the next one's


def _visit_link(crawl: _Crawl, tab_key: int, link: _Link) -> None:
    started = crawl.clock()
    try:
        final, title, snapshot, rows = _call(lambda live: _visit(live, tab_key, link.url))
    except (PageFailed, TimeoutError):
        crawl.result.failed += 1
        return
    if site_host(final) != crawl.host:  # sent to a login page or another site
        crawl.result.outside += 1
        return
    final_key = page_key(final)
    if final_key != page_key(link.url) and final_key in crawl.seen_pages and crawl.result.pages:
        return  # redirected to a page that is already mapped or queued
    crawl.seen_pages.add(final_key)
    items = _items(snapshot, rows, final)
    if crawl.shell is None:
        crawl.shell = shell_names(items)
    elif not inside_app(shell_names(items), crawl.shell):
        crawl.result.outside += 1
        crawl.outside_groups[link.group] += 1
        return
    index, kept = _record_page(crawl, link, final, title, items)
    _explore_menus(crawl, tab_key, final, index, kept, link)
    page = crawl.result.pages[index]
    crawl.result.pages[index] = CrawledPage(page.url, page.title, page.via, round(crawl.clock() - started, 1))


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
    heapq.heappush(crawl.queue, _Link(0, START_PRIORITY, 0, start, ""))
    try:
        while crawl.queue and len(crawl.result.pages) < crawl.max_pages:
            reason = crawl.stop_reason()
            if reason:
                crawl.result.partial = reason
                break
            link = heapq.heappop(crawl.queue)
            if link.group[1] >= 0 and crawl.outside_groups[link.group] >= OUTSIDE_PER_GROUP:
                crawl.result.skipped += 1  # its neighbours led outside the app (a footer)
                continue
            _visit_link(crawl, tab_key, link)
    finally:
        try:
            _call(lambda live: _close_tab(live, tab_key))
        except Exception:  # noqa: BLE001 - the map is still worth keeping
            pass
    return crawl.result
