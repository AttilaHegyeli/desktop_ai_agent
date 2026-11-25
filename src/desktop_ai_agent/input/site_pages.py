"""What one page of a site contains, and which of its links a site map follows (pure).

The site map (action/tools/site_crawler.py) reads each page as an accessibility snapshot,
the same text page_elements.py parses. This module turns that text into map items and
holds the link rules, so all of it is tested with plain strings:

  - items: named links, buttons, tabs, menu items, settings controls (check boxes,
    switches, fields) and headings, each with the page area it sits in: "nav"
    (navigation, header, sidebar, footer, menus, tab strips), "main" or "other";
  - content is left out: links whose address holds an ID (a video, a commit, an
    email), lists of many links that share one address pattern (a feed), long card
    titles, table rows and list options;
  - links that could change something (log out, delete, unsubscribe, download...)
    or lead to another site or a file are never followed;
  - "inside the app": a page that does not share the start page's header and sidebar
    (YouTube's legal and marketing pages) is not part of the map (owner, 2026-11-25:
    map the useful part, not the legal text).

The snapshot leaves out some facts (a menu entry's link address, whether a button opens
a menu, editable boxes). One fixed script, ELEMENT_FACTS_JS, reads them for the whole
page at once; they are matched to the snapshot by the element's box, which the
snapshot rounds exactly like Math.round (checked 2026-11-25).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import product
from urllib.parse import parse_qsl, urljoin, urlparse

from desktop_ai_agent.input.element_ranking import fold
from desktop_ai_agent.input.page_elements import (
    CLICKABLE_TYPE,
    SnapNode,
    clean_name,
    page_elements,
    short,
)

MAX_LABEL = 80
MAX_CARD_LABEL = 60  # a longer clickable in the page's main part is a content card
MAX_HEADING_LEVEL = 3
GROUP_MIN = 6  # this many links with one address pattern on a page = a content list
GROUP_LINKS = 3  # the nearest container with this many links is a link's group (a footer)
SHELL_MIN = 4  # a start page with fewer header/sidebar names: every page counts as inside
SHELL_SHARED = 5  # names a page must share with the start page's header/sidebar
REPEAT_MIN = 4  # look-alike items on one page that make a repeated row
MAX_AROUND = 50  # a node up to this many times bigger may stand for a marked element

NAV_ROLES = frozenset({
    "navigation", "banner", "complementary", "contentinfo", "menu", "menubar", "tablist", "tree",
})
MAIN_ROLES = frozenset({"main"})
SKIPPED_ROLES = frozenset({"row", "option", "listbox", "spinbutton"})
HEADING_KIND = "Heading"

# Address words of links that act instead of showing a page. Addresses are English on
# most sites even when the page text is not (YouTube's Hungarian "Kijelentkezés" -> /logout).
_RISKY_URL = re.compile(
    r"log-?out|log_out|sign-?out|sign_out|signoff|delete|remove|unsubscribe|deactivate|disconnect|"
    r"revoke|download|export|purchase|checkout|/buy\b|/pay\b",
    re.IGNORECASE,
)
_FILE_ENDINGS = (
    ".pdf", ".zip", ".rar", ".7z", ".exe", ".msi", ".dmg", ".apk", ".iso", ".gz", ".tar",
    ".mp3", ".mp4", ".mov", ".avi", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".csv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx",
)
# Query keys that name one piece of content or a search, not a section of the site.
_CONTENT_KEYS = frozenset({"v", "id", "list", "q", "query", "search", "search_query", "p", "page"})
# Query keys that only track the click or pick a language: the same page without them.
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "hl", "gl", "ref", "ref_src", "feature", "si"})
LINK_KINDS = frozenset({"Hyperlink", "MenuItem", CLICKABLE_TYPE, "TabItem", "TreeItem"})
# Form controls never take a link address from a surrounding link.
_NO_HREF_KINDS = frozenset({"Heading", "CheckBox", "RadioButton", "Edit", "SearchBox", "ComboBox", "Slider", "Spinner"})
SHELL_KINDS = frozenset({"Hyperlink", "Button", "MenuItem", "TabItem", CLICKABLE_TYPE})

Box = tuple[int, int, int, int]

# One call per page: [x, y, width, height, link address, opens a menu, editable,
# focusable] for every visible element that could matter, shadow roots included.
# "focusable" = reachable with Tab but not a link, button or field (YouTube's "Add a
# comment..." placeholder). Fixed text: nothing from the model is ever put in it.
ELEMENT_FACTS_JS = """() => {
  const rows = [];
  const pick = 'a[href], button, [role], [aria-haspopup], [aria-expanded], [contenteditable], [tabindex]';
  const visit = (root) => {
    for (const el of root.querySelectorAll(pick)) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const a = el.closest('a[href]') || el.querySelector('a[href]');
      const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
      const editable = el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable);
      const role = (el.getAttribute('role') || '').toLowerCase();
      const field = editable || role === 'textbox' || role === 'searchbox' || role === 'combobox';
      const focusable = el.hasAttribute('tabindex') && el.tabIndex >= 0 && !el.getAttribute('role')
        && !el.isContentEditable && !/^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY|IFRAME|VIDEO)$/.test(el.tagName);
      rows.push([Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height),
                 a ? a.href : '', (popup !== '' && popup !== 'false') || el.getAttribute('aria-expanded') === 'false',
                 editable, focusable, field ? (el.getAttribute('placeholder') || el.textContent || '').trim().slice(0, 60) : '']);
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) visit(el.shadowRoot);
  };
  visit(document);
  return rows;
}"""
_ID_LIKE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9_-]{7,}$|^\d{4,}$", re.IGNORECASE)


@dataclass(frozen=True)
class PageItem:
    label: str
    kind: str  # the page list's type names ("Hyperlink", "Button"...) or "Heading"
    ref: str  # Playwright aria-ref in the snapshot it came from
    url: str = ""  # absolute target of a link ("" otherwise)
    area: str = "other"  # "nav" | "main" | "other" ("menu" is set by the crawler)
    box: Box | None = None  # as the snapshot gives it (viewport pixels, rounded)
    group: int = -1  # snapshot index of the container it shares with other links (-1: none)
    popup: bool = False  # announces a menu or panel it opens (from ELEMENT_FACTS_JS)


@dataclass(frozen=True)
class Fact:
    href: str = ""
    popup: bool = False
    editable: bool = False
    focusable: bool = False
    text: str = ""  # a field's own text or placeholder ("Add a comment...") when it has no name


# ---- addresses ---------------------------------------------------------------------

def site_host(url: str) -> str:
    """Host without 'www.', lower case: one site whether or not the address has www."""
    host = (urlparse(url or "").hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, host: str) -> bool:
    return bool(host) and site_host(url) == host


def page_key(url: str) -> str:
    """One text per distinct page: host without www, path without a trailing '/', the
    query, and the #fragment only when it is a route ('#settings/general' in Gmail; a
    plain '#top' anchor is the same page)."""
    parts = urlparse(url or "")
    fragment = parts.fragment if "/" in parts.fragment else ""
    kept = [
        p for p in parts.query.split("&")
        if p and not _is_tracking(p.split("=", 1)[0])
    ]
    return "|".join((site_host(url), parts.path.rstrip("/") or "/", "&".join(sorted(kept)), fragment))


def _is_tracking(key: str) -> bool:
    key = key.casefold()
    return key.startswith("utm_") or key in _TRACKING_KEYS


def is_content_url(url: str) -> bool:
    """Does the address point at one piece of content (a video, a commit, a search)?"""
    parts = urlparse(url or "")
    segments = [s for s in parts.path.split("/") if s]
    if any(s.startswith("@") or _ID_LIKE.match(s) for s in segments):
        return True
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold() in _CONTENT_KEYS or _ID_LIKE.match(value):
            return True
    return False


def url_pattern(url: str) -> tuple[str, str, int, tuple[str, ...]]:
    """Links of one list share this: (host, first path part, number of parts, query keys)."""
    parts = urlparse(url or "")
    segments = [s for s in parts.path.split("/") if s]
    keys = tuple(sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}))
    return site_host(url), segments[0] if segments else "", len(segments), keys


def why_not_follow(url: str, host: str) -> str:
    """'' when a map may open this link, else the reason it may not."""
    parts = urlparse(url or "")
    if parts.scheme not in ("http", "https"):
        return "not a web page"
    if not same_site(url, host):
        return "another site"
    if _RISKY_URL.search(parts.path + "?" + parts.query):
        return "could change something"
    if parts.path.casefold().endswith(_FILE_ENDINGS):
        return "a file"
    if is_content_url(url):
        return "content"
    return ""


# ---- items -------------------------------------------------------------------------

def _area(index: int, nodes: list[SnapNode]) -> str:
    """The nearest landmark around node `index` decides its area."""
    parent = nodes[index].parent
    while parent is not None:
        role = nodes[parent].role
        if role in NAV_ROLES:
            return "nav"
        if role in MAIN_ROLES:
            return "main"
        parent = nodes[parent].parent
    return "other"


def _level(node: SnapNode) -> int:
    try:
        return int(node.attrs.get("level", "1"))
    except ValueError:
        return 1


def _box(node: SnapNode) -> Box | None:
    box = node.box
    return tuple(int(round(v)) for v in box) if box is not None else None  # type: ignore[return-value]


def _groups(nodes: list[SnapNode], indexes: list[int]) -> dict[int, int]:
    """node index -> its nearest ancestor holding GROUP_LINKS or more of `indexes`."""
    counts: Counter[int] = Counter()
    for i in indexes:
        parent = nodes[i].parent
        while parent is not None:
            counts[parent] += 1
            parent = nodes[parent].parent
    groups: dict[int, int] = {}
    for i in indexes:
        parent = nodes[i].parent
        while parent is not None and counts[parent] < GROUP_LINKS:
            parent = nodes[parent].parent
        groups[i] = parent if parent is not None else -1
    return groups


def page_items(nodes: list[SnapNode], base_url: str) -> list[PageItem]:
    """The page's map items in page order (content cards, rows and options left out)."""
    elements = {el.ref: el for el in page_elements(nodes)}
    found: list[tuple[int, PageItem]] = []
    for i, node in enumerate(nodes):
        if not node.ref:
            continue
        area = _area(i, nodes)
        if node.role == "heading":
            label = clean_name(node.name or node.text or " ".join(node.texts))
            if label and _level(node) <= MAX_HEADING_LEVEL:
                found.append((i, PageItem(short(label, MAX_LABEL), HEADING_KIND, node.ref, "", area, _box(node))))
            continue
        el = elements.get(node.ref)
        if el is None or node.role in SKIPPED_ROLES:
            continue
        if el.type == CLICKABLE_TYPE and area == "main" and len(el.name) > MAX_CARD_LABEL:
            continue
        link = node.props.get("url", "")
        url = urljoin(base_url, link) if link else ""
        found.append((i, PageItem(short(el.name, MAX_LABEL), el.type, node.ref, url, area, _box(node))))
    groups = _groups(nodes, [i for i, it in found if it.url or it.kind in LINK_KINDS])
    return [replace(it, group=groups[i]) if i in groups else it for i, it in found]


# ---- facts the snapshot leaves out -------------------------------------------------

def facts_by_box(rows: list[list[object]] | None) -> dict[Box, Fact]:
    """ELEMENT_FACTS_JS rows by box. Elements sharing a box (a menu entry and the link
    inside it) are merged: the first address, and "opens a menu" when any of them does."""
    facts: dict[Box, Fact] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            x, y, w, h, href, popup, editable, *rest = row
            box = (int(x), int(y), int(w), int(h))  # type: ignore[call-overload]
        except (TypeError, ValueError):
            continue
        href = href if isinstance(href, str) and href.startswith(("http://", "https://")) else ""
        focusable = bool(rest[0]) if rest else False
        text = rest[1] if len(rest) > 1 and isinstance(rest[1], str) else ""
        old = facts.get(box)
        if old is None:
            facts[box] = Fact(href, bool(popup), bool(editable), focusable, text)
        else:
            facts[box] = Fact(
                old.href or href, old.popup or bool(popup), old.editable or bool(editable),
                old.focusable or focusable, old.text or text,
            )
    return facts


@dataclass(frozen=True)
class Marks:
    """Snapshot nodes the page script marked (see marked_refs)."""

    editable: frozenset[str] = frozenset()
    focusable: frozenset[str] = frozenset()
    # ref -> where to click inside it (x, y from its top-left): the marked element is
    # smaller than the node the snapshot shows for it (an inline placeholder in a line).
    points: dict[str, tuple[int, int]] = field(default_factory=dict)
    # ref -> the field's own text, for fields the snapshot shows without a name (YouTube's
    # comment box is `textbox` with nothing else: "Add a comment..." is not in the snapshot).
    names: dict[str, str] = field(default_factory=dict)


def _contains(outer: Box, inner: Box) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox - 1 <= ix and oy - 1 <= iy and ix + iw <= ox + ow + 1 and iy + ih <= oy + oh + 1


def marked_refs(nodes: list[SnapNode], facts: dict[Box, Fact]) -> Marks:
    """Editable boxes and focusable placeholders among the snapshot nodes.

    A node matches by its box. When the snapshot shows a marked element only as part of
    a bigger node (an inline "Add a comment..." placeholder inside its line), the
    smallest node around it is marked, with the point to click at.
    """
    editable: set[str] = set()
    focusable: set[str] = set()
    points: dict[str, tuple[int, int]] = {}
    boxed = [(node, box) for node in nodes if node.ref and (box := _box(node)) is not None]
    matched: set[Box] = set()
    for node, box in boxed:
        key = _fact_key(box, facts)
        fact = facts.get(key) if key is not None else None
        if fact is None or not (fact.editable or fact.focusable):
            continue
        matched.add(key)  # type: ignore[arg-type]
        (editable if fact.editable else focusable).add(node.ref)
    for fbox, fact in facts.items():
        if fbox in matched or not (fact.editable or fact.focusable):
            continue
        area = max(fbox[2] * fbox[3], 1)
        around = [(n, b) for n, b in boxed if _contains(b, fbox) and b[2] * b[3] <= MAX_AROUND * area]
        if not around:
            continue
        node, box = min(around, key=lambda nb: nb[1][2] * nb[1][3])
        (editable if fact.editable else focusable).add(node.ref)
        points[node.ref] = (fbox[0] - box[0] + fbox[2] // 2, fbox[1] - box[1] + fbox[3] // 2)
    names: dict[str, str] = {}
    for node, box in boxed:
        if node.name:
            continue
        key = _fact_key(box, facts)
        text = facts[key].text if key is not None else ""
        if text:
            names[node.ref] = text
    return Marks(frozenset(editable), frozenset(focusable), points, names)


def _fact_key(box: Box, facts: dict[Box, Fact]) -> Box | None:
    if box in facts:
        return box
    for delta in product((-1, 0, 1), repeat=4):
        near = tuple(v + d for v, d in zip(box, delta))
        if near in facts:
            return near  # type: ignore[return-value]
    return None


def fact_for(box: Box | None, facts: dict[Box, Fact]) -> Fact | None:
    """The fact of an element's box (exactly, else within 1 pixel: rounding differs at .5)."""
    if box is None:
        return None
    exact = facts.get(box)
    if exact is not None:
        return exact
    for delta in product((-1, 0, 1), repeat=4):
        near = facts.get(tuple(v + d for v, d in zip(box, delta)))  # type: ignore[arg-type]
        if near is not None:
            return near
    return None


def with_facts(items: list[PageItem], facts: dict[Box, Fact]) -> list[PageItem]:
    """Items with the link addresses and menu flags the snapshot did not show."""
    out: list[PageItem] = []
    for it in items:
        fact = fact_for(it.box, facts)
        if fact is None:
            out.append(it)
            continue
        url = it.url or (fact.href if it.kind not in _NO_HREF_KINDS else "")
        out.append(replace(it, url=url, popup=fact.popup))
    return out


# ---- repeated rows and titles --------------------------------------------------------

_COUNT_PREFIX = re.compile(r"^\(\d+\)\s*")  # "(2769) YouTube": the unread count changes by the minute


def clean_title(title: str) -> str:
    return _COUNT_PREFIX.sub("", title or "").strip()


def drop_repeated_rows(items: list[PageItem]) -> list[PageItem]:
    """Keep one of every list of look-alike buttons and links.

    A feed gives every channel or video the same buttons ("Subscribe to: X", "Go to
    channel: X"): 4 or more items of one page that start with the same three words are
    one repeated row, not 4 places of the site (owner's YouTube map: 42% of its items).
    Menus are left alone. Language independent: it only compares words.
    """
    def prefix(it: PageItem) -> tuple[str, ...]:
        words = fold(it.label).split()
        return tuple(words[:3]) if len(words) >= 4 else ()

    counts = Counter(prefix(it) for it in items if prefix(it) and it.area != "menu")
    seen: set[tuple[str, ...]] = set()
    kept: list[PageItem] = []
    for it in items:
        key = prefix(it)
        if key and it.area != "menu" and counts[key] >= REPEAT_MIN:
            if key in seen:
                continue
            seen.add(key)
        kept.append(it)
    return kept


# ---- inside the app ------------------------------------------------------------------

def shell_names(items: list[PageItem]) -> frozenset[str]:
    """Names of a page's header, sidebar and menus: what every page of the app repeats."""
    return frozenset(fold(it.label) for it in items if it.area in ("nav", "other") and it.kind in SHELL_KINDS)


def inside_app(names: frozenset[str], shell: frozenset[str]) -> bool:
    """Does a page with these header/sidebar names belong to the app of `shell` (the
    start page's)? Legal and marketing pages have a header of their own."""
    if len(shell) < SHELL_MIN:
        return True
    need = min(SHELL_SHARED, max(2, len(shell) // 2))
    return len(names & shell) >= need


def content_patterns(items: list[PageItem]) -> set[tuple[str, str, int, tuple[str, ...]]]:
    """Address patterns shared by GROUP_MIN or more links in a page's main part: content
    lists (one owner's repositories). Menus and sidebars are left out: a long menu
    (/feed/history, /feed/playlists...) is the site's structure, not content."""
    counts = Counter(url_pattern(it.url) for it in items if it.url and it.area == "main")
    return {pattern for pattern, n in counts.items() if n >= GROUP_MIN}


def is_content(item: PageItem, patterns: set[tuple[str, str, int, tuple[str, ...]]]) -> bool:
    if not item.url:
        return False
    return is_content_url(item.url) or (item.area == "main" and url_pattern(item.url) in patterns)
