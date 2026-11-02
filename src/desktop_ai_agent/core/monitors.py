"""Monitor and DPI detection (Windows, ctypes only, no new dependencies).

Why it exists: a mouse tool must know the real pixel size of the screen, where each
monitor sits (positions can be negative on multi-monitor setups) and the Windows
scale percentage. All coordinates here are PHYSICAL pixels, which is only true once
the process has declared itself DPI-aware (enable_dpi_awareness(), called first in
main(), before any window is created).

Every public function is safe to call anywhere: failures become warnings or empty
results, never exceptions. The two small functions that really call Windows
(_enumerate_raw, _apply_dpi_awareness) are kept thin so everything else is testable
with fake data.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable

LogFn = Callable[[str], None]

_dpi_label = "not set"
_monitors: list["Monitor"] | None = None
_preference: str | int = "primary"


@dataclass(frozen=True)
class Monitor:
    index: int  # 0 = primary, then left-to-right, top-to-bottom
    device: str
    left: int  # screen coordinates of the top-left corner (may be negative)
    top: int
    width: int  # real pixels
    height: int
    scale_percent: int | None  # 100, 125, 150 ... or None if Windows would not tell
    primary: bool

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


# ---- pure helpers (fully testable) --------------------------------------------

def build_monitors(raw: list[dict[str, Any]]) -> list[Monitor]:
    """Turn raw Windows rows into Monitor objects. Bad rows are skipped."""
    rows: list[tuple[bool, int, int, int, int, int | None, str]] = []
    for item in raw:
        try:
            left, top, right, bottom = (int(item[k]) for k in ("left", "top", "right", "bottom"))
        except (KeyError, TypeError, ValueError):
            continue
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            continue
        dpi = item.get("dpi")
        scale = round(int(dpi) * 100 / 96) if isinstance(dpi, (int, float)) and dpi > 0 else None
        rows.append((bool(item.get("primary")), left, top, width, height, scale, str(item.get("device") or "")))
    rows.sort(key=lambda r: (not r[0], r[1], r[2]))  # primary first, then by position
    return [
        Monitor(i, device, left, top, width, height, scale, primary)
        for i, (primary, left, top, width, height, scale, device) in enumerate(rows)
    ]


def select_monitor(monitors: list[Monitor], preference: str | int = "primary") -> Monitor | None:
    """Pick the monitor to work on. Unknown or out-of-range preferences fall back to primary."""
    if not monitors:
        return None
    primary = next((m for m in monitors if m.primary), monitors[0])
    pref: Any = preference
    if isinstance(pref, str):
        text = pref.strip().lower()
        if text in ("", "primary"):
            return primary
        if not text.isdigit():
            return primary
        pref = int(text)
    if isinstance(pref, bool) or not isinstance(pref, int):
        return primary
    for monitor in monitors:
        if monitor.index == pref:
            return monitor
    return primary


def _requested_index(preference: str | int) -> int | None:
    if isinstance(preference, bool):
        return None
    if isinstance(preference, int):
        return preference
    text = str(preference).strip()
    return int(text) if text.isdigit() else None


def virtual_desktop(monitors: list[Monitor]) -> tuple[int, int, int, int] | None:
    """Bounding box of all monitors: (left, top, width, height)."""
    if not monitors:
        return None
    left = min(m.left for m in monitors)
    top = min(m.top for m in monitors)
    right = max(m.right for m in monitors)
    bottom = max(m.bottom for m in monitors)
    return left, top, right - left, bottom - top


def _describe(monitor: Monitor) -> str:
    scale = f"@{monitor.scale_percent}%" if monitor.scale_percent is not None else "@scale unknown"
    text = f"#{monitor.index} {monitor.width}x{monitor.height} {scale}"
    if monitor.left or monitor.top:
        text += f" at ({monitor.left}, {monitor.top})"
    if monitor.primary:
        text += " (primary)"
    return text


def format_monitors(monitors: list[Monitor]) -> str:
    """One log line, e.g. `Monitors: 1 — #0 2560x1440 @150% (primary)`."""
    if not monitors:
        return "Monitors: none detected"
    return f"Monitors: {len(monitors)} — " + ", ".join(_describe(m) for m in monitors)


def monitor_warnings(monitors: list[Monitor]) -> list[str]:
    out: list[str] = []
    if not monitors:
        return out  # detect_monitors() already explained why
    if not any(m.primary for m in monitors):
        out.append("Windows reported no primary monitor; using the first one")
    unknown = [m.index for m in monitors if m.scale_percent is None]
    if unknown:
        out.append(
            "Scale percentage unknown for monitor(s) "
            + ", ".join(f"#{i}" for i in unknown)
            + " (DPI query unavailable)"
        )
    scales = {m.scale_percent for m in monitors if m.scale_percent is not None}
    if len(scales) > 1:
        out.append(
            "Monitors use different scale percentages ("
            + ", ".join(f"{s}%" for s in sorted(scales))
            + "); keep coordinates per monitor"
        )
    return out


def capture_mismatch(
    monitors: list[Monitor],
    capture_size: tuple[int, int] | None = None,
) -> str | None:
    """Warning text if the screenshot library sees a different screen size than Windows.

    A mismatch usually means the process is not DPI-aware, and clicks would land in
    the wrong place. Returns None when sizes agree or the check cannot be made.
    """
    desktop = virtual_desktop(monitors)
    if desktop is None:
        return None
    size = capture_size if capture_size is not None else _mss_virtual_size()
    if size is None:
        return None
    _, _, width, height = desktop
    if (width, height) == (int(size[0]), int(size[1])):
        return None
    return (
        f"Screen size mismatch: Windows reports {width}x{height} but screenshot capture "
        f"reports {size[0]}x{size[1]}. This usually means the process is not DPI-aware; "
        "mouse positions would be off."
    )


def _mss_virtual_size() -> tuple[int, int] | None:
    try:
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[0]  # the whole virtual desktop
            return int(monitor["width"]), int(monitor["height"])
    except Exception:  # noqa: BLE001 — mss missing or capture unavailable: skip the check
        return None


# ---- detection and cache -------------------------------------------------------

def detect_monitors(*, warnings: list[str] | None = None) -> list[Monitor]:
    """Ask Windows for the monitor layout. Never raises; returns [] when unavailable."""
    warn = warnings if warnings is not None else []
    if sys.platform != "win32":
        warn.append("Monitor detection is only available on Windows")
        return []
    try:
        found = build_monitors(_enumerate_raw())
    except Exception as exc:  # noqa: BLE001
        warn.append(f"Monitor detection failed: {exc}")
        return []
    if not found:
        warn.append("Windows reported no monitors")
    return found


def set_monitors(monitors: list[Monitor], *, preference: str | int = "primary") -> None:
    """Cache what setup detected, plus the user's monitor preference."""
    global _monitors, _preference
    _monitors = list(monitors)
    _preference = preference


def get_monitors(*, refresh: bool = False) -> list[Monitor]:
    """Cached monitors. refresh=True re-detects (cheap; use right before acting)."""
    global _monitors
    if refresh or _monitors is None:
        _monitors = detect_monitors()
    return list(_monitors)


def target_monitor(*, refresh: bool = False) -> Monitor | None:
    return select_monitor(get_monitors(refresh=refresh), _preference)


def report_at_setup(emit: LogFn, *, preference: str | int = "primary") -> list[Monitor]:
    """Detect, cache and log everything setup should show. Returns the monitors."""
    warnings: list[str] = []
    found = detect_monitors(warnings=warnings)
    set_monitors(found, preference=preference)

    label = dpi_awareness_label()
    emit(f"DPI awareness: {label}")
    emit(format_monitors(found))

    target = select_monitor(found, preference)
    requested = _requested_index(preference)
    if target is not None and requested is not None and target.index != requested:
        warnings.append(f"Requested monitor {requested} not found; using the primary monitor")
    elif target is not None and not target.primary:
        emit(f"Target monitor: #{target.index}")

    warnings.extend(monitor_warnings(found))
    if label == "unavailable":
        warnings.append("Could not enable DPI awareness; mouse positions may be wrong above 100% scale")
    mismatch = capture_mismatch(found)
    if mismatch:
        warnings.append(mismatch)
    for message in warnings:
        emit(f"WARNING: {message}")
    return found


# ---- DPI awareness -------------------------------------------------------------

def dpi_awareness_label() -> str:
    return _dpi_label


def enable_dpi_awareness() -> str:
    """Declare the process DPI-aware. Call first in main(), before any window exists.

    Returns a short label and remembers it for dpi_awareness_label(). Never raises.
    """
    global _dpi_label
    if sys.platform != "win32":
        _dpi_label = "not applicable (not Windows)"
        return _dpi_label
    try:
        _dpi_label = _apply_dpi_awareness()
    except Exception:  # noqa: BLE001
        _dpi_label = "unavailable"
    return _dpi_label


# ---- the only code that really calls Windows (thin on purpose) -------------------

def _apply_dpi_awareness() -> str:  # pragma: no cover - needs Windows
    import ctypes

    error_access_denied = 5
    e_accessdenied = -2147024891  # 0x80070005 as a signed HRESULT

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # 1) Windows 10 (1703+): per-monitor v2
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_int
        ctypes.set_last_error(0)
        if fn(ctypes.c_void_p(-4)):
            return "per-monitor v2"
        if ctypes.get_last_error() == error_access_denied:
            return "already set by the host process"
    except (AttributeError, OSError):
        pass

    # 2) Windows 8.1+: per-monitor
    try:
        shcore = ctypes.WinDLL("shcore")
        fn = shcore.SetProcessDpiAwareness
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_long
        result = fn(2)
        if result == 0:
            return "per-monitor"
        if result == e_accessdenied:
            return "already set by the host process"
    except (AttributeError, OSError):
        pass

    # 3) Vista+: system DPI aware
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unavailable"


def _enumerate_raw() -> list[dict[str, Any]]:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        shcore = ctypes.WinDLL("shcore")
    except OSError:
        shcore = None

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM
    )
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        monitor_enum_proc,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    if shcore is not None:
        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        shcore.GetDpiForMonitor.restype = ctypes.c_long

    primary_flag = 1  # MONITORINFOF_PRIMARY
    effective_dpi = 0  # MDT_EFFECTIVE_DPI
    raw: list[dict[str, Any]] = []

    def callback(hmonitor: Any, _hdc: Any, _rect: Any, _lparam: Any) -> bool:
        try:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                dpi: int | None = None
                if shcore is not None:
                    dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
                    if shcore.GetDpiForMonitor(
                        hmonitor, effective_dpi, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
                    ) == 0:
                        dpi = int(dpi_x.value)
                rect = info.rcMonitor
                raw.append(
                    {
                        "device": info.szDevice,
                        "left": rect.left,
                        "top": rect.top,
                        "right": rect.right,
                        "bottom": rect.bottom,
                        "primary": bool(info.dwFlags & primary_flag),
                        "dpi": dpi,
                    }
                )
        except Exception:  # noqa: BLE001 — a callback must never raise into Windows
            pass
        return True

    callback_ref = monitor_enum_proc(callback)  # keep a reference alive during the call
    user32.EnumDisplayMonitors(None, None, callback_ref, 0)
    return raw