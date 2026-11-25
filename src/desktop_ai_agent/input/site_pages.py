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
    or lead to another site or a file are never followed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, urljoin, urlparse

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
_ID_LIKE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9_-]{7,}$|^\d{4,}$", re.IGNORECASE)


@dataclass(frozen=True)
class PageItem:
    label: str
    kind: str  # the page list's type names ("Hyperlink", "Button"...) or "Heading"
    ref: str  # Playwright aria-ref in the snapshot it came from
    url: str = ""  # absolute target of a link ("" otherwise)
    area: str = "other"  # "nav" | "main" | "other" ("menu" is set by the crawler)


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
    query = "&".join(sorted(parts.query.split("&"))) if parts.query else ""
    return "|".join((site_host(url), parts.path.rstrip("/") or "/", query, fragment))


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


def page_items(nodes: list[SnapNode], base_url: str) -> list[PageItem]:
    """The page's map items in page order (content cards, rows and options left out)."""
    elements = {el.ref: el for el in page_elements(nodes)}
    items: list[PageItem] = []
    for i, node in enumerate(nodes):
        if not node.ref:
            continue
        area = _area(i, nodes)
        if node.role == "heading":
            label = clean_name(node.name or node.text or " ".join(node.texts))
            if label and _level(node) <= MAX_HEADING_LEVEL:
                items.append(PageItem(short(label, MAX_LABEL), HEADING_KIND, node.ref, "", area))
            continue
        el = elements.get(node.ref)
        if el is None or node.role in SKIPPED_ROLES:
            continue
        if el.type == CLICKABLE_TYPE and area == "main" and len(el.name) > MAX_CARD_LABEL:
            continue
        link = node.props.get("url", "")
        url = urljoin(base_url, link) if link else ""
        items.append(PageItem(short(el.name, MAX_LABEL), el.type, node.ref, url, area))
    return items


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
