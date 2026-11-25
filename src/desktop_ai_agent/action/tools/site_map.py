"""Site maps: find where something is on a site, and introduce a site (LLM path).

  site_map(action="map")      explore the site of the page in front (site_crawler.py),
                              embed every name on the CPU (core/embeddings.py), save the map
  site_map(action="find")     where is X: word matches first, then matches by meaning;
                              the top 3 with the way to reach each, and match 1 is shown on
                              the page right away (outlined, numbered for click)
  site_map(action="show")     show match 2 or 3 of the last find
  site_map(action="outline")  a short overview the model introduces the site from

Rules kept in code (owner's decisions, 2026-11-25):
  - a map is made only when the user's message asks for it ("map this site");
  - after a plain map the turn ends with a fixed reply, so Gemma does no extra round;
  - Google work sites + a cloud model: matches and outlines need the user's OK first
    (page_control.check_cloud), like reading those pages;
  - maps live outside the repo: %LOCALAPPDATA%\\desktop_ai_agent\\site_maps\\<site>.json;
  - Stop keeps what was mapped so far, marked partial.
"""
from __future__ import annotations

import json
import re
import threading
from array import array
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from desktop_ai_agent.action.tools import browser_session, page_control, site_crawler
from desktop_ai_agent.action.tools.site_crawler import PATH_SEP, CrawledItem, CrawledPage, CrawlResult
from desktop_ai_agent.core import embeddings
from desktop_ai_agent.input.element_ranking import fold, meaningful_words, words, words_match
from desktop_ai_agent.input.page_elements import PageElement, clean_name, short
from desktop_ai_agent.input.site_pages import site_host

ACTIONS = ("map", "find", "show", "outline")
MAP_VERSION = 1
TOP_HITS = 3
KEYWORD_WEIGHT = 0.5  # all words of the request in a name ~ a clearly better meaning score
EXACT_BONUS = 1.0  # the name IS the request: always first
CONTEXT_WEIGHT = 0.2  # where an item sits (its page path, its menu) counts a little too
LOOSE_SCORE = 0.5  # best match below this and no word match: only a loose match (measured 2026-11-25)
OUTLINE_CHARS_LOCAL = 1600  # ~400 tokens: Gemma has an 8k context
OUTLINE_CHARS_CLOUD = 6000  # ~1,500 tokens
MAP_MINUTES = "about 2 minutes"

_ASKS_TO_MAP = re.compile(r"\b(re-?)?map(ping)?\b", re.IGNORECASE)

_cancel = threading.Event()  # set by Stop (service.stop); the crawl checks it between pages
_last_hits: tuple[str, list[int]] | None = None  # (site, item indexes) of the last find


@dataclass
class SiteMap:
    host: str
    start: str
    mapped_at: str
    pages: list[CrawledPage]
    items: list[CrawledItem]
    partial: str = ""
    model: str = ""  # the embedding model of `vectors` ("" when there are none)
    vectors: list[Any] | None = None  # one normalized vector per item, or None
    context_vectors: dict[str, Any] | None = None  # item_context text -> its vector


@dataclass(frozen=True)
class Hit:
    index: int  # into SiteMap.items
    score: float
    words: bool  # at least one word of the request is in the name


def cancel() -> None:
    """Stop button: a running crawl ends after the page it is on (the map is kept, partial)."""
    _cancel.set()


def allow() -> None:
    """A new task starts: mapping may run again."""
    _cancel.clear()


def reset() -> None:
    """New chat: forget the last find."""
    global _last_hits
    _last_hits = None


# ---- seams (the tests replace these) -----------------------------------------------

def _root() -> Path:
    return browser_session.profile_root().parent / "site_maps"


def _crawl(should_stop: Callable[[], bool]) -> CrawlResult:
    return site_crawler.crawl_site(should_stop)


def _embed(texts: Sequence[str], model: str | None, should_stop: Callable[[], bool] | None = None) -> list[list[float]]:
    return embeddings.embed(texts, model=model, should_stop=should_stop)


def _current_page(handover_hwnd: int | None) -> tuple[str, str, str]:
    return page_control.current_page(handover_hwnd)


def _show_item(url: str, label: str, kind: str, menu: str) -> tuple[PageElement | None, dict[str, Any]]:
    return page_control.show_item(url, label, kind, menu)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---- storage -----------------------------------------------------------------------

def map_path(host: str) -> Path:
    name = re.sub(r"[^a-z0-9.-]", "_", (host or "").casefold()) or "site"
    return _root() / f"{name}.json"


def save_map(site: SiteMap) -> Path:
    vectors = site.vectors or []
    contexts = site.context_vectors or {}
    data = {
        "version": MAP_VERSION,
        "host": site.host,
        "start": site.start,
        "mapped_at": site.mapped_at,
        "partial": site.partial,
        "model": site.model if vectors else "",
        "pages": [asdict(p) for p in site.pages],
        "items": [asdict(it) for it in site.items],
        "dim": len(vectors[0]) if vectors else 0,
        "vectors": embeddings.pack(vectors) if vectors else "",
        "contexts": list(contexts),
        "context_vectors": embeddings.pack(list(contexts.values())) if vectors and contexts else "",
    }
    path = map_path(site.host)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)  # a half-written file never replaces a good map
    return path


def load_map(host: str) -> SiteMap | None:
    """The saved map of `host`, or None (no map, unreadable, or an older format)."""
    try:
        data = json.loads(map_path(host).read_text(encoding="utf-8"))
        if data.get("version") != MAP_VERSION:
            return None
        pages = [CrawledPage(**p) for p in data["pages"]]
        items = [CrawledItem(**it) for it in data["items"]]
        vectors = embeddings.unpack(data["vectors"], int(data["dim"])) if data.get("vectors") else None
        texts = [str(t) for t in data.get("contexts") or []]
        blob = data.get("context_vectors") or ""
        context_list = embeddings.unpack(blob, int(data["dim"])) if blob and vectors else []
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if vectors is not None and len(vectors) != len(items):
        vectors = None
    contexts = dict(zip(texts, context_list)) if vectors and len(context_list) == len(texts) else None
    return SiteMap(
        str(data["host"]), str(data["start"]), str(data["mapped_at"]), pages, items,
        str(data.get("partial") or ""), str(data.get("model") or "") if vectors else "", vectors,
        contexts or None,
    )


# ---- building and matching ---------------------------------------------------------

def item_context(pages: Sequence[CrawledPage], item: CrawledItem) -> str:
    """Where an item sits, as text: its page's path (or title) and its menu.

    Two items with one name ("Értesítések": a bell button and a link on the settings
    page) then differ, and the one in the right place wins (tested 2026-11-25)."""
    page = pages[item.page]
    return PATH_SEP.join(x for x in (page.via or page.title, item.menu) if x)


def build_map(crawl: CrawlResult, should_stop: Callable[[], bool] | None = None) -> tuple[SiteMap, str]:
    """(the map, why matching by meaning is off: '' when it works)."""
    model, _host = embeddings.settings()
    labels = [it.label for it in crawl.items]
    contexts = list(dict.fromkeys(item_context(crawl.pages, it) for it in crawl.items))
    texts = list(dict.fromkeys(labels + contexts))  # a header repeated on every page is embedded once
    vectors: list[Any] | None = None
    context_vectors: dict[str, Any] | None = None
    note = ""
    try:
        by_text = dict(zip(texts, _embed(texts, model, should_stop))) if texts else {}
        if labels:
            vectors = [array("f", by_text[label]) for label in labels]
            context_vectors = {c: array("f", by_text[c]) for c in contexts}
    except embeddings.EmbeddingError as exc:
        note = str(exc)
    site = SiteMap(
        crawl.host, crawl.start_url, _now_text(), list(crawl.pages), list(crawl.items),
        crawl.partial, model if vectors else "", vectors, context_vectors,
    )
    return site, note


def rank(site: SiteMap, query: str, query_vector: Sequence[float] | None, top: int = TOP_HITS) -> list[Hit]:
    """Best items for `query`: name == request, then word matches, then meaning.

    One hit per name: a name repeated on several pages ("Settings" link, menu item and
    page heading) is shown once, at its first place in the map (the shortest way there).
    """
    asked = meaningful_words(query) or words(query)
    exact = fold(clean_name(query))
    use_meaning = query_vector is not None and site.vectors is not None
    places: dict[str, float] = {}  # context text -> its similarity, computed once per query
    if use_meaning and site.context_vectors:
        places = {text: embeddings.dot(query_vector, v) for text, v in site.context_vectors.items()}  # type: ignore[arg-type]
    hits: list[Hit] = []
    for i, item in enumerate(site.items):
        named = words(item.label)
        found = sum(1 for w in asked if any(words_match(w, n, typos=True) for n in named))
        score = KEYWORD_WEIGHT * found / len(asked) if asked else 0.0
        if use_meaning:
            score += embeddings.dot(query_vector, site.vectors[i])  # type: ignore[index]
            score += CONTEXT_WEIGHT * places.get(item_context(site.pages, item), 0.0)
        if fold(item.label) == exact:
            score += EXACT_BONUS
        hits.append(Hit(i, score, found > 0))
    hits.sort(key=lambda h: (-h.score, h.index))
    picked: list[Hit] = []
    names: set[str] = set()
    for hit in hits:
        if hit.score <= 0:
            break
        name = fold(site.items[hit.index].label)
        if name in names:
            continue
        names.add(name)
        picked.append(hit)
        if len(picked) == top:
            break
    return picked


def hit_line(number: int, site: SiteMap, index: int) -> str:
    """'1: MenuItem "Settings" in menu "Account" on page "YouTube"' (+ how to reach the page)."""
    item = site.items[index]
    page = site.pages[item.page]
    title = short(page.title or page.url, 50)
    where = f'in menu "{short(item.menu, 40)}" on page "{title}"' if item.menu else f'on page "{title}"'
    path = f" (page reached by: {short(page.via, 80)})" if page.via else ""
    return f'{number}: {item.kind} "{item.label}" {where}{path}'


def _names(values: list[str], room: int) -> str:
    """'a, b, c and 4 more' within about `room` characters."""
    out: list[str] = []
    used = 0
    for i, value in enumerate(values):
        if out and used + len(value) + 2 > room:
            return ", ".join(out) + f" and {len(values) - i} more"
        out.append(value)
        used += len(value) + 2
    return ", ".join(out)


def outline(site: SiteMap, cloud: bool = False) -> str:
    """A short overview of the site for the model to introduce it (bigger for cloud models)."""
    budget = OUTLINE_CHARS_CLOUD if cloud else OUTLINE_CHARS_LOCAL
    start = site.pages[0] if site.pages else CrawledPage(site.start, site.host)
    head = (
        f'Site {site.host} ("{short(start.title, 60)}"), mapped {site.mapped_at}: '
        f"{len(site.pages)} pages, {len(site.items)} items" + (f" (partial: {site.partial})" if site.partial else "")
    )
    lines = [head]
    room = budget - len(head)

    def unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(v for v in values if v))

    sections = unique([
        it.label for it in site.items
        if it.page == 0 and it.area == "nav" and it.kind in ("Hyperlink", "TabItem", "MenuItem")
    ])
    headings = unique([it.label for it in site.items if it.page == 0 and it.kind == "Heading"])
    menus: dict[str, list[str]] = {}
    for it in site.items:
        if it.menu:
            menus.setdefault(it.menu, []).append(it.label)
    blocks: list[tuple[str, list[str], float]] = [
        ("Main sections", sections, 0.3),
        ("Start page headings", headings, 0.15),
    ]
    for menu, labels in list(menus.items())[:6]:
        blocks.append((f'Menu "{short(menu, 40)}"', unique(labels), 0.1))
    for title, values, share in blocks:
        if values and room > 80:
            line = f"{title}: " + _names(values, int(budget * share))
            lines.append(line)
            room -= len(line) + 1
    pages = [p for p in site.pages[1:] if p.title]
    if pages and room > 80:
        lines.append("Pages (title, way there):")
        room -= 26
        for i, page in enumerate(pages):
            line = f'- "{short(page.title, 60)}" ({short(page.via, 70)})' if page.via else f'- "{short(page.title, 60)}"'
            if room - len(line) - 1 < 0:
                lines.append(f"- and {len(pages) - i} more pages")
                break
            lines.append(line)
            room -= len(line) + 1
    return "\n".join(lines)


# ---- the tool ----------------------------------------------------------------------

def asks_to_map(request: str) -> bool:
    return bool(_ASKS_TO_MAP.search(request or ""))


def _mapped_reply(site: SiteMap, note: str) -> str:
    count = f"{len(site.pages)} page{'s' if len(site.pages) != 1 else ''}"
    if site.partial:
        why = "it was stopped" if site.partial == "stopped" else "the time limit"
        reply = (
            f"I mapped part of the site ({count}) before {why}. "
            "I can introduce what I found or find sections you want."
        )
    else:
        reply = f"The site is mapped ({count}). I can introduce it to you or find sections you want."
    if note:
        reply += f" (Finding by meaning is off: {note}.)"
    return reply


def _query_vector(site: SiteMap, query: str) -> tuple[list[float] | None, str]:
    if site.vectors is None:
        return None, "this map has no meaning data, so only words were compared"
    try:
        return _embed([query], site.model or None)[0], ""
    except (embeddings.EmbeddingError, IndexError) as exc:
        return None, f"only words were compared ({exc})"


def _show(site: SiteMap, index: int) -> tuple[PageElement | None, dict[str, Any]]:
    item = site.items[index]
    page = site.pages[item.page]
    try:
        return _show_item(page.url, item.label, item.kind, item.menu)
    except (LookupError, RuntimeError, TimeoutError) as exc:
        return None, {"shown": False, "show_error": str(exc)[:200]}


def _show_note(number: int, shown: PageElement | None, result: dict[str, Any], site: SiteMap) -> str:
    if shown is not None:
        note = (
            f"Match {number} is outlined in red on the page; it is element {shown.id} (click it only if "
            "the user wants it opened)."
        )
    elif result.get("shown"):
        note = f"Match {number} is outlined in red on the page."
    else:
        note = (
            f"Match {number} was not found on the page now (the site may have changed since it was mapped "
            f"on {site.mapped_at}): tell the user where it is from its match line."
        )
    if result.get("menu_not_found"):
        note += f" Its menu button '{result['menu_not_found']}' was not found."
    return note


def _find(site: SiteMap, query: str) -> tuple[dict[str, Any], PageElement | None]:
    global _last_hits
    vector, meaning_note = _query_vector(site, query)
    hits = rank(site, query, vector)
    result: dict[str, Any] = {"site": site.host, "find": query}
    if not hits:
        result["matches"] = ""
        result["note"] = (
            f"Nothing on the map of {site.host} matches '{query}'. Tell the user; it may be in a part "
            "that was not mapped."
        )
        return result, None
    _last_hits = (site.host, [h.index for h in hits])
    result["matches"] = "\n".join(hit_line(n, site, h.index) for n, h in enumerate(hits, 1))
    shown, show_result = _show(site, hits[0].index)
    result.update(show_result)
    notes = [_show_note(1, shown, show_result, site)]
    if len(hits) > 1:
        notes.append("If it is not what the user meant, call site_map with action show and number 2 or 3.")
    best = hits[0]
    if not best.words and best.score < LOOSE_SCORE:
        notes.append("These are only loose matches: the site may not have it, or it is in a part that was not mapped.")
    if site.partial:
        notes.append("The map is partial.")
    if meaning_note:
        notes.append(f"Finding by meaning is off: {meaning_note}.")
    result["note"] = " ".join(notes)
    return result, shown


def _map(find: str | None, request: str, cloud: bool, handover_hwnd: int | None) -> tuple[dict[str, Any], PageElement | None]:
    if not asks_to_map(request):
        raise PermissionError(
            "A site is mapped only when the user asks for it (for example 'map this site'). Ask the user "
            f"whether to map it; it takes {MAP_MINUTES}."
        )
    _current_page(handover_hwnd)  # the user's own browser in front: its page is handed over first
    crawl = _crawl(_cancel.is_set)
    if not crawl.pages:
        raise RuntimeError(
            f"The site could not be mapped: none of its pages could be read ({crawl.failed} failed). "
            "It may need the user to log in first in the agent's browser."
        )
    site, note = build_map(crawl, _cancel.is_set)
    save_map(site)
    summary: dict[str, Any] = {"mapped": site.host, "pages": len(site.pages), "items": len(site.items)}
    if site.partial:
        summary["partial"] = site.partial
    if not find:
        return {**summary, "final_reply": _mapped_reply(site, note)}, None
    try:
        page_control.check_cloud(site.start, cloud, request)
    except PermissionError as exc:
        return {**summary, "note": f"{exc} Then call site_map with action find."}, None
    result, shown = _find(site, find)
    if note:
        result["note"] = result.get("note", "") + f" (Meaning data could not be made: {note}.)"
    return {**summary, **result}, shown


def run(
    action: str,
    find: str | None = None,
    number: int | None = None,
    request: str = "",
    cloud: bool = False,
    handover_hwnd: int | None = None,
) -> tuple[dict[str, Any], PageElement | None]:
    """The site_map tool: (result for the model, the shown element when it can be clicked)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {list(ACTIONS)}, got '{action}'")
    if action == "map":
        return _map(find, request, cloud, handover_hwnd)
    if action == "show":
        return _show_again(number, request, cloud)
    url, _title, _note = _current_page(handover_hwnd)
    host = site_host(url)
    site = load_map(host) if host else None
    if site is None:
        raise LookupError(
            f"{host or 'This page'} has no site map yet. Ask the user whether to map it (they say 'map this "
            f"site'; it takes {MAP_MINUTES})."
        )
    page_control.check_cloud(site.start, cloud, request)
    if action == "outline":
        return {"site": site.host, "outline": outline(site, cloud)}, None
    if not find or not find.strip():
        raise ValueError("find needs what to look for in the user's words, e.g. find='privacy settings'.")
    return _find(site, find.strip())


def _show_again(number: int | None, request: str, cloud: bool) -> tuple[dict[str, Any], PageElement | None]:
    if _last_hits is None:
        raise LookupError("There is no earlier find to show a match from. Call site_map with action find first.")
    host, indexes = _last_hits
    if number is None or not 1 <= number <= len(indexes):
        raise ValueError(f"number must be between 1 and {len(indexes)} (a match of the last find).")
    site = load_map(host)
    if site is None or indexes[number - 1] >= len(site.items):
        raise LookupError(f"The map of {host} changed or is gone. Call site_map with action find again.")
    page_control.check_cloud(site.start, cloud, request)
    shown, result = _show(site, indexes[number - 1])
    out = {"site": host, "match": hit_line(number, site, indexes[number - 1]), **result}
    out["note"] = _show_note(number, shown, result, site)
    return out, shown
