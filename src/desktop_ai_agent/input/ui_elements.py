"""Read the clickable elements on screen through Windows UI Automation (read-only).

Windows already knows every button, menu item, list item ... with its name and exact
rectangle. Matching a request like "the volume icon" to a NAME is a text task, which a
small model does far better than guessing pixels from a screenshot.

How: the COM UI Automation API (UIAutomationCore.dll) through `comtypes`
(pip install -e ".[ui]"). The older .NET API that PowerShell offers cannot see taskbar
buttons or desktop icons, so it is not used. Everything except the two thin functions
at the bottom is pure and tested with fake rows. Nothing here clicks, focuses or
changes anything.

Coordinates are screen pixels, possibly negative on multi-monitor setups. They match
our screenshots only when the process is DPI aware (core.monitors.enable_dpi_awareness(),
called first in main()).
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

# UI Automation control type ids worth pointing at (clicked or typed into).
CONTROL_TYPES = {
    50000: "Button",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50007: "ListItem",
    50011: "MenuItem",
    50013: "RadioButton",
    50019: "TabItem",
    50024: "TreeItem",
    50031: "SplitButton",
}

# Top-level window class -> friendlier name for the model.
_APP_ALIASES = {"Shell_TrayWnd": "taskbar", "Progman": "desktop", "WorkerW": "desktop"}

# UI Automation property ids
_NAME, _CONTROL_TYPE, _RECT, _OFFSCREEN = 30005, 30003, 30001, 30022

_DESKTOP_CLASSES = frozenset({"Progman", "WorkerW"})

Scanner = Callable[[int], list[dict[str, Any]]]  # max_elements -> raw rows
# screen point -> (root window handle, its class) visible there, or None if unknown
RootAtFn = Callable[[int, int], "tuple[int, str] | None"]


@dataclass(frozen=True)
class UiElement:
    id: int  # 1-based, stable within one scan only
    name: str
    type: str  # "Button", "ListItem", ...
    app: str  # top-level window name, or "taskbar" / "desktop"
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2


# ---- pure helpers ----------------------------------------------------------------

def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _clean(value: Any) -> str:
    """Collapse whitespace and drop invisible format characters (the taskbar clock's
    name contains left-to-right marks that break consoles and confuse the model)."""
    text = "".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def is_visible_at(row_hwnd: int, row_cls: str, found: tuple[int, str] | None) -> bool:
    """Does the top-level window that is actually visible at a point belong to this row?

    `found` is (root window handle, its class) at the element's centre, or None if
    unknown (then the element is kept). Desktop icons may be hosted by Progman or by a
    WorkerW window, so those two count as the same window.
    """
    if found is None or not row_hwnd:
        return True
    hwnd, cls = found
    return hwnd == row_hwnd or (cls in _DESKTOP_CLASSES and row_cls in _DESKTOP_CLASSES)


def parse_elements(
    rows: list[Any],
    bounds: tuple[int, int, int, int] | None = None,
    min_size: int = 4,
    root_at: RootAtFn | None = None,
    stats: dict[str, int] | None = None,
) -> list[UiElement]:
    """Turn raw scan rows into numbered elements.

    A row is {"name", "type" (control type id or name), "app", "cls", "hwnd", "left",
    "top", "right", "bottom"}. Drops rows that are malformed, of an unwanted type,
    smaller than `min_size` px, outside `bounds` ((left, top, right, bottom)), or exact
    duplicates. With `root_at`, also drops elements hidden behind another window (their
    centre shows a different window); `stats["hidden"]` counts those.
    """
    if stats is not None:
        stats.setdefault("hidden", 0)
    seen: set[tuple[Any, ...]] = set()
    elements: list[UiElement] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ctype = row.get("type")
        ctype = CONTROL_TYPES.get(ctype) if isinstance(ctype, int) else str(ctype or "")
        if not ctype:
            continue
        rect = [_int(row.get(k)) for k in ("left", "top", "right", "bottom")]
        if any(v is None for v in rect):
            continue
        left, top, right, bottom = rect  # type: ignore[misc]
        if right - left < min_size or bottom - top < min_size:
            continue
        if bounds is not None and (
            right <= bounds[0] or bottom <= bounds[1] or left >= bounds[2] or top >= bounds[3]
        ):
            continue
        cls = str(row.get("cls") or "")
        if root_at is not None:
            hwnd = _int(row.get("hwnd")) or 0
            if not is_visible_at(hwnd, cls, root_at((left + right) // 2, (top + bottom) // 2)):
                if stats is not None:
                    stats["hidden"] += 1
                continue
        name = _clean(row.get("name"))
        app = _APP_ALIASES.get(cls) or _clean(row.get("app")) or cls
        key = (name, ctype, left, top, right, bottom)
        if key in seen:
            continue
        seen.add(key)
        elements.append(UiElement(len(elements) + 1, name, ctype, app, left, top, right, bottom))
    return elements


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def screen_extent(elements: list[UiElement]) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) around all elements: a stand-in for the screen."""
    if not elements:
        return None
    return (min(e.left for e in elements), min(e.top for e in elements),
            max(e.right for e in elements), max(e.bottom for e in elements))


def position_label(el: UiElement, screen: tuple[int, int, int, int]) -> str:
    """Rough place on screen in a 3 x 3 grid: 'top-left', 'center', 'bottom-right', ...

    Cheap way to tell apart elements with the same name (two "Close" buttons).
    """
    left, top, right, bottom = screen
    cx, cy = el.center
    col = min(2, max(0, (cx - left) * 3 // max(1, right - left)))
    row = min(2, max(0, (cy - top) * 3 // max(1, bottom - top)))
    vertical, horizontal = ("top", "middle", "bottom")[row], ("left", "center", "right")[col]
    if (vertical, horizontal) == ("middle", "center"):
        return "center"
    return f"{vertical}-{horizontal}"


def format_for_model(
    elements: list[UiElement],
    max_items: int = 400,
    max_name: int = 60,
    screen: tuple[int, int, int, int] | None = None,
) -> tuple[str, int]:
    """One line per NAMED element, e.g. '17: Button "Volume" (taskbar, bottom-right)'.

    Unnamed elements are left out (the model cannot match them by text). `screen` is
    (left, top, right, bottom) for the position hints; by default the area all elements
    span. Returns the text and how many elements it lists (at most `max_items`).
    """
    area = screen or screen_extent(elements)
    lines: list[str] = []
    for el in elements:
        if not el.name:
            continue
        if len(lines) >= max_items:
            break
        where = [_short(el.app, 40)] if el.app else []
        if area is not None:
            where.append(position_label(el, area))
        suffix = f" ({', '.join(where)})" if where else ""
        lines.append(f'{el.id}: {el.type} "{_short(el.name, max_name)}"{suffix}')
    return "\n".join(lines), len(lines)


def match_element(old: UiElement, fresh: list[UiElement], tolerance: int = 20) -> UiElement | None:
    """Find `old` again in a newer scan, or None if it is gone or has moved.

    IDs are only valid within one scan, so before acting on an element the caller
    rescans and checks: same name, type and window, centre within `tolerance` px.
    If several qualify, the nearest wins.
    """
    ox, oy = old.center
    best: tuple[float, UiElement] | None = None
    for el in fresh:
        if (el.name, el.type, el.app) != (old.name, old.type, old.app):
            continue
        x, y = el.center
        distance = max(abs(x - ox), abs(y - oy))
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, el)
    return best[1] if best else None


_ID = re.compile(r'"id"\s*:\s*(null|-?\d+)', re.IGNORECASE)


def parse_choice(text: str) -> tuple[str, int | None]:
    """Read the model's pick: ("id", n), ("none", None) for {"id": null}, or ("invalid", None)."""
    if not text:
        return "invalid", None
    match = _ID.search(text)
    if not match:
        return "invalid", None
    value = match.group(1).lower()
    return ("none", None) if value == "null" else ("id", int(value))


def find_element(elements: list[UiElement], element_id: int | None) -> UiElement | None:
    return next((el for el in elements if el.id == element_id), None)


def list_ui_elements(
    *,
    bounds: tuple[int, int, int, int] | None = None,
    max_elements: int = 1500,
    timeout: float = 10.0,
    scanner: Scanner | None = None,
    visible_only: bool = True,
    root_at: RootAtFn | None = None,
    stats: dict[str, int] | None = None,
) -> list[UiElement]:
    """Scan the screen once. Raises RuntimeError with a readable reason on failure.

    The scan runs on a worker thread so a hung application cannot block the caller
    longer than `timeout` (the thread is then abandoned; it is read-only).
    With `visible_only`, elements hidden behind other windows are dropped (`root_at`
    defaults to asking Windows; tests pass a fake). `stats["hidden"]` counts them.
    """
    if visible_only and root_at is None:
        root_at = _root_window_at
    scan = scanner or _scan_rows
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["rows"] = scan(max_elements)
        except Exception as exc:  # noqa: BLE001 - reported below
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="uia-scan")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise RuntimeError(f"UI Automation scan took longer than {timeout:g}s")
    if "error" in box:
        exc = box["error"]
        if isinstance(exc, ImportError):
            raise RuntimeError('UI Automation needs comtypes: pip install -e ".[ui]"') from None
        raise RuntimeError(f"UI Automation scan failed: {type(exc).__name__}: {exc}") from None
    return parse_elements(box.get("rows") or [], bounds=bounds,
                          root_at=root_at if visible_only else None, stats=stats)


# ---- Windows (thin, not unit tested) ----------------------------------------------

def _uia() -> tuple[Any, Any]:  # pragma: no cover - needs Windows + comtypes
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")  # generates bindings once, then cached
    from comtypes.gen import UIAutomationClient as U

    return U, comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)


def _scan_rows(max_elements: int) -> list[dict[str, Any]]:  # pragma: no cover - needs Windows
    import gc

    import comtypes

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)  # this is a worker thread
    try:
        return _collect_rows(max_elements)
    finally:
        # Every COM object must be released BEFORE CoUninitialize, or a later release crashes.
        gc.collect()
        comtypes.CoUninitialize()


def _collect_rows(max_elements: int) -> list[dict[str, Any]]:  # pragma: no cover - needs Windows
    """Plain dict rows only: no COM object may escape this function."""
    U, uia = _uia()
    any_type = None
    for type_id in CONTROL_TYPES:
        cond = uia.CreatePropertyCondition(_CONTROL_TYPE, type_id)
        any_type = cond if any_type is None else uia.CreateOrCondition(any_type, cond)
    cond = uia.CreateAndCondition(any_type, uia.CreatePropertyCondition(_OFFSCREEN, False))
    cache = uia.CreateCacheRequest()  # fetch all properties in one cross-process trip
    for prop in (_NAME, _CONTROL_TYPE, _RECT):
        cache.AddProperty(prop)

    rows: list[dict[str, Any]] = []
    windows = uia.GetRootElement().FindAll(U.TreeScope_Children, uia.CreateTrueCondition())
    for i in range(windows.Length):
        if len(rows) >= max_elements:
            break
        win = windows.GetElement(i)
        try:
            app, cls = win.CurrentName or "", win.CurrentClassName or ""
            hwnd = int(win.CurrentNativeWindowHandle or 0)
            # After "Show desktop" the icon list itself appears as a top-level element;
            # use its real root window so it matches what _root_window_at reports.
            if hwnd:
                hwnd, cls = _root_of(hwnd) or (hwnd, cls)
            found = win.FindAllBuildCache(U.TreeScope_Descendants, cond, cache)
        except Exception:  # noqa: BLE001 - a window closed or refused; skip it
            continue
        for j in range(found.Length if found else 0):
            if len(rows) >= max_elements:
                break
            try:
                el = found.GetElement(j)
                r = el.CachedBoundingRectangle
                rows.append({
                    "name": el.CachedName or "", "type": el.CachedControlType, "app": app, "cls": cls, "hwnd": hwnd,
                    "left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
                })
            except Exception:  # noqa: BLE001
                continue
    return rows


_user32: Any = None


def _u32() -> Any:  # pragma: no cover - needs Windows
    """Own user32 handle with explicit signatures (never touches ctypes.windll's shared one)."""
    global _user32
    if _user32 is None:
        import ctypes
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        _user32 = u
    return _user32


def _root_of(hwnd: int) -> tuple[int, str] | None:  # pragma: no cover - needs Windows
    """(root top-level window handle, its class name) for any window handle."""
    import ctypes

    u = _u32()
    root = u.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    if not root:
        return None
    buf = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(root, buf, 256)
    return int(root), buf.value


def _root_window_at(x: int, y: int) -> tuple[int, str] | None:  # pragma: no cover - needs Windows
    """(top-level window handle, class name) of the window visible at screen point (x, y)."""
    from ctypes import wintypes

    hwnd = _u32().WindowFromPoint(wintypes.POINT(x, y))
    return _root_of(int(hwnd)) if hwnd else None
