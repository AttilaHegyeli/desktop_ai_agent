"""Turn a web page's accessibility snapshot into numbered elements (pure, no browser).

The browser side (action/tools/browser_session.py) asks Playwright for
``page.aria_snapshot(mode="ai", boxes=True)``: one YAML-like line per accessible node,
for example::

    - link "Home" [ref=e5] [cursor=pointer] [box=12,80,64,20]:
      - /url: /
    - textbox "Search" [ref=e9] [box=300,10,400,32]
    - generic [ref=e40] [cursor=pointer] [box=0,900,300,60]:
      - text: Some card title

Everything here works on that text only, so it is tested with plain strings. The model
sees the same line format as the desktop list (``N: Type "name"``); the ``ref`` stays
on our side and is how a number is clicked later (``aria-ref=e9``).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

MAX_NAME = 45  # web names are long (video titles); 60 made a YouTube list ~1.5k tokens

# Roles the user can act on. Rows are included because e.g. Gmail's inbox lists each
# email as a row of a grid; grid cells are left out (one email would become 6 lines).
INTERACTIVE_ROLES = frozenset({
    "link", "button", "textbox", "searchbox", "combobox", "listbox", "checkbox", "radio",
    "switch", "slider", "spinbutton", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "row", "treeitem",
})

# ARIA role -> our element type name (same names as the desktop list where they exist).
ROLE_TYPES = {
    "link": "Hyperlink",
    "button": "Button",
    "textbox": "Edit",
    "searchbox": "SearchBox",
    "combobox": "ComboBox",
    "listbox": "List",
    "checkbox": "CheckBox",
    "radio": "RadioButton",
    "switch": "CheckBox",
    "slider": "Slider",
    "spinbutton": "Spinner",
    "tab": "TabItem",
    "menuitem": "MenuItem",
    "menuitemcheckbox": "MenuItem",
    "menuitemradio": "MenuItem",
    "option": "ListItem",
    "row": "Row",
    "treeitem": "TreeItem",
}

# Input fields type_text may write into on a web page.
TEXT_TYPES = frozenset({"Edit", "SearchBox", "ComboBox"})

# Pointer-cursor containers (clickable divs without a role) get this type.
CLICKABLE_TYPE = "Clickable"
EDITABLE_NAME = "Text box"  # an editable box with no name and no text yet
MAX_FOCUSABLE_NAME = 60  # longer text on a focusable element: a whole panel, not a placeholder

# Roles that never become their own line, even with a pointer cursor.
_NOT_ELEMENTS = frozenset({"text", "img", "image", "presentation", "none", "paragraph", "heading"})


@dataclass
class SnapNode:
    depth: int
    role: str
    name: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""  # inline text after "key: "
    texts: list[str] = field(default_factory=list)  # "- text:" children
    props: dict[str, str] = field(default_factory=dict)  # "- /url:" and "- /placeholder:" children
    parent: int | None = None  # index of the parent node in the flat list

    @property
    def ref(self) -> str:
        return self.attrs.get("ref", "")

    @property
    def pointer(self) -> bool:
        return self.attrs.get("cursor") == "pointer"

    @property
    def box(self) -> tuple[float, float, float, float] | None:
        raw = self.attrs.get("box")
        if not raw:
            return None
        try:
            x, y, w, h = (float(v) for v in raw.split(","))
        except ValueError:
            return None
        return x, y, w, h


@dataclass(frozen=True)
class PageElement:
    id: int  # 1-based, page order; stable within one snapshot only
    name: str
    type: str
    role: str
    ref: str  # Playwright aria-ref of the latest snapshot, e.g. "e12"
    page_key: int = 0  # which tab it belongs to (browser_session's key)
    where: str = ""  # "" (in view) | "below" | "above"
    checked: bool = False
    disabled: bool = False
    placeholder: str = ""
    click_at: tuple[int, int] | None = None  # point inside it to click (from its top-left); None = centre


# ---- text helpers ------------------------------------------------------------------

def clean_name(value: object) -> str:
    """Collapse whitespace and drop invisible format characters (as ui_elements does)."""
    text = "".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def short(text: str, limit: int = MAX_NAME) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def estimate_tokens(text: str) -> int:
    return len(text) // 4  # same rough rule as orchestrator/agent_loop.py


# ---- snapshot parsing --------------------------------------------------------------

_ROLE = re.compile(r"[a-z][a-z-]*")
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_REGEX_NAME = re.compile(r"/(?:[^/\\]|\\.)*/")
_ATTR = re.compile(r"\[([a-z-]+)(?:=([^\]]*))?\]")


def _yaml_single_quoted(item: str) -> tuple[str, str]:
    """("'key'" + rest) -> (key, rest). '' inside the quotes is one quote."""
    out: list[str] = []
    i = 1
    while i < len(item):
        if item[i] == "'":
            if item[i + 1 : i + 2] != "'":
                return "".join(out), item[i + 1 :]
            i += 1
        out.append(item[i])
        i += 1
    return "".join(out), ""


def _unquote_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, str) else value
        except ValueError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_key(key: str) -> tuple[str, str, dict[str, str], str] | None:
    """'link "Home" [ref=e5]: Home' -> ('link', 'Home', {'ref': 'e5'}, 'Home').

    Returns None for lines that are not a role (e.g. a bare string). The last item is
    the inline text after ':' ('' when there is none).
    """
    rest = key.strip()
    if rest.startswith("'"):
        inner, after = _yaml_single_quoted(rest)
        rest = inner + after
    match = _ROLE.match(rest)
    if match is None:
        return None
    role, i = match.group(0), match.end()
    name = ""
    if rest[i : i + 2] == ' "':
        quoted = _JSON_STRING.match(rest, i + 1)
        if quoted:
            name = _unquote_value(quoted.group(0))
            i = quoted.end()
    elif rest[i : i + 2] == " /":
        regex = _REGEX_NAME.match(rest, i + 1)
        if regex:
            name = regex.group(0)[1:-1]
            i = regex.end()
    attrs: dict[str, str] = {}
    while True:
        while rest[i : i + 1] == " ":
            i += 1
        attr = _ATTR.match(rest, i)
        if attr is None:
            break
        attrs[attr.group(1)] = attr.group(2) if attr.group(2) is not None else "true"
        i = attr.end()
    tail = rest[i:].strip()
    if tail and not tail.startswith(":"):
        return None  # something we do not understand: skip the line rather than guess
    return role, name, attrs, _unquote_value(tail[1:]) if tail else ""


def parse_snapshot(snapshot: str) -> list[SnapNode]:
    """Flat list of role nodes in page order, each knowing its parent and child texts."""
    nodes: list[SnapNode] = []
    stack: list[int] = []  # indexes of the open ancestors, by depth
    for raw in (snapshot or "").splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        depth = (len(raw) - len(stripped)) // 2
        item = stripped[2:]
        while stack and nodes[stack[-1]].depth >= depth:
            stack.pop()
        parent = stack[-1] if stack else None
        if item.startswith("text:"):
            if parent is not None:
                nodes[parent].texts.append(clean_name(_unquote_value(item[5:])))
            continue
        if item.startswith("/"):
            prop, _, value = item[1:].partition(":")
            if parent is not None and prop in ("url", "placeholder"):
                nodes[parent].props[prop] = clean_name(_unquote_value(value))
            continue
        parsed = parse_key(item)
        if parsed is None:
            continue
        role, name, attrs, text = parsed
        nodes.append(SnapNode(depth, role, clean_name(name), attrs, clean_name(text), parent=parent))
        stack.append(len(nodes) - 1)
    return nodes


# ---- picking the elements ----------------------------------------------------------

def type_for_role(role: str) -> str:
    return ROLE_TYPES.get(role) or role[:1].upper() + role[1:]


def _inside_kept(index: int, nodes: list[SnapNode], kept: set[int]) -> bool:
    parent = nodes[index].parent
    while parent is not None:
        if parent in kept:
            return True
        parent = nodes[parent].parent
    return False


def _text_within(index: int, nodes: list[SnapNode], limit: int = 120) -> str:
    """All text of node `index` and its descendants (they follow it, deeper), cut to `limit`."""
    depth = nodes[index].depth
    pieces: list[str] = []
    j = index
    while j < len(nodes) and (j == index or nodes[j].depth > depth):
        node = nodes[j]
        pieces.extend(p for p in (node.name if j != index else "", node.text, *node.texts) if p)
        if sum(len(p) + 1 for p in pieces) > limit:
            break
        j += 1
    return clean_name(" ".join(pieces))[:limit]


def _where(box: tuple[float, float, float, float] | None, viewport_height: float | None) -> str:
    if box is None or not viewport_height:
        return ""
    _x, y, _w, h = box
    if y >= viewport_height:
        return "below"
    if y + h <= 0:
        return "above"
    return ""


def page_elements(
    nodes: list[SnapNode],
    viewport_height: float | None = None,
    page_key: int = 0,
    editable_refs: frozenset[str] = frozenset(),
    focusable_refs: frozenset[str] = frozenset(),
    field_names: dict[str, str] | None = None,
) -> list[PageElement]:
    """Named elements the user can act on, in page order, exact duplicates dropped.

    Kept: interactive roles with a ref, and the OUTERMOST pointer-cursor container that
    is not already inside a kept element (CSS passes the pointer cursor down to every
    child, so only the outer one is the real button). Unnamed fields use their
    placeholder; unnamed pointer containers use their own text. Zero-size boxes are
    dropped: nothing there can be clicked.

    The snapshot does not mark editable boxes (YouTube's comment box is a plain
    "generic") or keyboard-focusable placeholders that open one ("Add a comment...").
    The page script finds those; `editable_refs` become Edit fields, `focusable_refs`
    Clickable ones. `field_names` names fields the snapshot shows without a name.
    """
    field_names = field_names or {}
    kept: set[int] = set()
    found: list[PageElement] = []
    seen: set[tuple[str, str]] = set()
    for i, node in enumerate(nodes):
        if not node.ref or node.role in _NOT_ELEMENTS:
            continue
        box = node.box
        if box is not None and (box[2] <= 0 or box[3] <= 0):
            continue
        if node.ref in editable_refs and node.role not in INTERACTIVE_ROLES:
            kind = "Edit"
            name = node.name or node.props.get("placeholder", "") or _text_within(i, nodes) or field_names.get(node.ref, "") or EDITABLE_NAME
        elif node.role in INTERACTIVE_ROLES:
            kind = type_for_role(node.role)
            name = node.name or node.props.get("placeholder", "") or field_names.get(node.ref, "") or (_text_within(i, nodes) if node.role == "row" else "")
        elif (node.pointer or node.ref in focusable_refs) and not _inside_kept(i, nodes, kept):
            parent = node.parent
            if parent is not None and nodes[parent].pointer:
                continue  # an inner part of a clickable container
            kind = CLICKABLE_TYPE
            name = node.name or _text_within(i, nodes)
            if not node.pointer and len(name) > MAX_FOCUSABLE_NAME:
                continue  # a focusable container (a whole panel), not something to click
        else:
            continue
        kept.add(i)
        name = clean_name(name)
        if not name or (kind, name) in seen:
            continue
        seen.add((kind, name))
        found.append(PageElement(
            id=len(found) + 1,
            name=name,
            type=kind,
            role=node.role,
            ref=node.ref,
            page_key=page_key,
            where=_where(box, viewport_height),
            checked=node.attrs.get("checked") == "true",
            disabled=node.attrs.get("disabled") == "true",
            placeholder=node.props.get("placeholder", ""),
        ))
    return found


def format_line(el: PageElement) -> str:
    """'12: Button "Search"', with '(below)' / '(checked)' style hints when useful."""
    hints = [h for h in (el.where, "checked" if el.checked else "", "disabled" if el.disabled else "") if h]
    suffix = f" ({', '.join(hints)})" if hints else ""
    return f'{el.id}: {el.type} "{short(el.name)}"{suffix}'


def format_page_list(elements: list[PageElement]) -> str:
    return "\n".join(format_line(el) for el in elements)


# ---- page text ---------------------------------------------------------------------

def split_parts(text: str, size: int) -> list[str]:
    """Cut `text` into parts of at most `size` characters, at spaces where possible."""
    text = clean_name(text)
    parts: list[str] = []
    while text:
        if len(text) <= size:
            parts.append(text)
            break
        cut = text.rfind(" ", 0, size + 1)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts


def find_snippets(text: str, query: str, radius: int = 150, max_hits: int = 5) -> list[str]:
    """Short pieces of `text` around each place `query` appears (case ignored)."""
    text, needle = clean_name(text), clean_name(query).casefold()
    if not needle:
        return []
    folded = text.casefold()
    spans: list[list[int]] = []
    start = folded.find(needle)
    while start != -1 and len(spans) < max_hits:
        lo, hi = max(0, start - radius), min(len(text), start + len(needle) + radius)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = hi  # overlaps the previous snippet: merge
        else:
            spans.append([lo, hi])
        start = folded.find(needle, start + len(needle))
    return [
        ("..." if lo > 0 else "") + text[lo:hi].strip() + ("..." if hi < len(text) else "")
        for lo, hi in spans
    ]
