from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DRIVES = ("C", "D", "E")
PROGRAM_DIR_NAMES = ("Program Files", "Program Files (x86)")
GAMES_DIR_NAMES = ("Games", "games")

# Junk / non-app launchers — prefix match on stem.
_SKIP_EXE_PREFIXES = (
    "unins",
    "uninstall",
    "setup",
    "update",
    "updater",
    "installer",
    "crash",
    "helper",
    "vcredist",
    "vc_redist",
    "dxsetup",
    "dotnetfx",
)

# Substrings that mark helpers / installers even mid-name (FooSetup, BraveCrashHandler).
# "unins" covers Uninstall*.exe, unins000.exe, Foo_uninstall.exe — never index these.
_SKIP_EXE_CONTAINS = (
    "unins",
    "uninstall",
    "setup",
    "installer",
    "updater",
    "updatebroker",
    "crashhandler",
    "crashpad",
    "crashreporter",
    "vcredist",
    "vc_redist",
    "dotnetfx",
    "dxsetup",
    "ipcbroker",
    "elevation_service",
    "notification_helper",
    "wireguard_service",
    "helper",
    "elevated_tracing",
)

# OS stubs used by "Uninstall X" registry icons — never treat as apps.
_SKIP_EXE_NAMES = frozenset(
    {
        "msiexec.exe",
        "rundll32.exe",
        "control.exe",
        "dllhost.exe",
        "explorer.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
    }
)

# Nested dirs that are almost never the app the user wants to open.
_LOW_PRIORITY_PATH_PARTS = frozenset(
    {
        "bin",
        "resources",
        "cli-plugins",
        "plugins",
        "helpers",
        "helper",
        "frontend",
        "support files",
        "runtime",
    }
)

# Directories to not descend into while walking (includes temp/cache).
_SKIP_DIR_NAMES = frozenset(
    {
        "windows",
        "$recycle.bin",
        "system volume information",
        "node_modules",
        ".git",
        "__pycache__",
        "winsxs",
        "temp",
        "tmp",
        "cache",
        "caches",
        # Dev / SDK trees dump hundreds of CLI tools that are not apps to open.
        "mingw64",
        "mingw32",
        "msys64",
        "msys32",
        "usr",
        "windows kits",
        "android-sdk",
        "build-tools",
        "platform-tools",
        "cmake",
        "common files",
        "installer",
        "redistributables",
        "redist",
        "crashpad",
        "crashreporter",
        "update",
        "updates",
        "cli-plugins",
        "resources",
    }
)

# Path segments that mark an .exe as non-app even if we already reached it
# (Start Menu target under Update\, Git mingw tool, …). Omits temp/tmp so
# unit-test fixtures under the OS temp dir still count as usable.
_SKIP_PATH_PARTS = frozenset(
    {
        "mingw64",
        "mingw32",
        "msys64",
        "msys32",
        "usr",
        "windows kits",
        "android-sdk",
        "build-tools",
        "platform-tools",
        "common files",
        "installer",
        "redistributables",
        "redist",
        "crashpad",
        "crashreporter",
        "update",
        "updates",
        "winsxs",
        "node_modules",
        "cli-plugins",
    }
)

# Top-level Program Files product folders that are almost never user apps.
_SKIP_PRODUCT_FOLDER_NAMES = frozenset(
    {
        "windows kits",
        "android",
        "dotnet",
        "common files",
        "msbuild",
        "reference assemblies",
        "windows defender",
        "windows mail",
        "windows media player",
        "windows nt",
        "windows photo viewer",
        "windowsportabledevices",
        "windowspowershell",
        "internet explorer",
    }
)

_NAME_RE = re.compile(r"[^a-z0-9]+")

DEFAULT_MAX_EXE_DEPTH = 4
DEFAULT_MAX_EXES = 20_000


@dataclass
class IndexResult:
    """Indexed entries plus human-readable setup warnings."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_name(raw: str) -> str:
    """Turn 'Microsoft VS Code' / 'Spotify' into vs_code / spotify style keys."""
    cleaned = _NAME_RE.sub("_", raw.strip().lower()).strip("_")
    for prefix in ("microsoft_", "google_", "adobe_", "oracle_"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 2:
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned or "unknown"


def compact_name(raw: str) -> str:
    """Collapse separators so 'MongoDB Compass' matches 'MongoDBCompass'."""
    return normalize_name(raw).replace("_", "")


def norm_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return os.path.normpath(str(path.absolute()))


def existing_drives(
    drives: Iterable[str] = DRIVES,
    *,
    warnings: list[str] | None = None,
) -> list[str]:
    """Return drive letters that exist; append warnings for missing ones."""
    found: list[str] = []
    warn = warnings if warnings is not None else []
    for raw in drives:
        letter = str(raw).rstrip(":").upper()
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                found.append(letter)
            else:
                warn.append(f"Drive {letter}: not found — skipped")
        except OSError as exc:
            warn.append(f"Drive {letter}: inaccessible ({exc}) — skipped")
    return found


def discover_program_roots(
    drives: tuple[str, ...] = DRIVES,
    *,
    warnings: list[str] | None = None,
    check_drives: bool = True,
) -> list[Path]:
    roots: list[Path] = []
    warn = warnings if warnings is not None else []
    letters = existing_drives(drives, warnings=warn) if check_drives else [
        str(d).rstrip(":").upper() for d in drives
    ]
    for drive in letters:
        for folder in PROGRAM_DIR_NAMES:
            path = Path(f"{drive}:/") / folder
            try:
                if path.is_dir():
                    roots.append(path)
            except OSError as exc:
                warn.append(f"Inaccessible: {path} ({exc})")
    return roots


def discover_games_roots(
    drives: tuple[str, ...] = DRIVES,
    *,
    warnings: list[str] | None = None,
    check_drives: bool = True,
) -> list[Path]:
    """Find Games/games under drive roots and under Users/*/."""
    roots: list[Path] = []
    warn = warnings if warnings is not None else []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = norm_path(path).lower()
        if key in seen:
            return
        try:
            if path.is_dir():
                seen.add(key)
                roots.append(path)
        except OSError as exc:
            warn.append(f"Inaccessible: {path} ({exc})")

    letters = existing_drives(drives, warnings=warn) if check_drives else [
        str(d).rstrip(":").upper() for d in drives
    ]
    for drive in letters:
        drive_root = Path(f"{drive}:/")
        for name in GAMES_DIR_NAMES:
            _add(drive_root / name)

        users = drive_root / "Users"
        try:
            if not users.is_dir():
                continue
            with os.scandir(users) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.lower() in _SKIP_DIR_NAMES | {
                        "public",
                        "default",
                        "default user",
                        "all users",
                    }:
                        continue
                    for name in GAMES_DIR_NAMES:
                        _add(Path(entry.path) / name)
        except OSError as exc:
            warn.append(f"Inaccessible: {users} ({exc})")

    return roots


def discover_start_menu_roots(*, warnings: list[str] | None = None) -> list[Path]:
    warn = warnings if warnings is not None else []
    candidates: list[Path] = []
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        candidates.append(
            Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )

    roots: list[Path] = []
    for path in candidates:
        try:
            if path.is_dir():
                roots.append(path)
        except OSError as exc:
            warn.append(f"Inaccessible: {path} ({exc})")
    return roots


def _looks_like_uninstall(text: str) -> bool:
    """True if a name/path/label is an uninstaller entry."""
    compact = re.sub(r"[^a-z0-9]+", "", text.strip().lower())
    return "unins" in compact


def _is_usable_exe(path: Path) -> bool:
    """True for real application launchers; false for installers/helpers/redists."""
    name = path.name.lower()
    if not name.endswith(".exe"):
        return False
    if name in _SKIP_EXE_NAMES:
        return False
    stem = path.stem.lower()
    if any(stem.startswith(p) for p in _SKIP_EXE_PREFIXES):
        return False
    # Component hosts: com.docker.backend.exe, com.adobe.* — not user apps.
    if stem.startswith("com."):
        return False
    # Never index uninstallers (any form of unins/uninstall in the name).
    if _looks_like_uninstall(stem) or _looks_like_uninstall(name):
        return False
    # Trailing junk patterns: FooSetup.exe, Bar_uninstall.exe
    if stem.endswith(
        ("_setup", "-setup", "_installer", "-installer", "_uninstall", "-uninstall")
    ):
        return False
    compact = stem.replace("_", "").replace("-", "").replace(" ", "")
    if any(token in compact for token in _SKIP_EXE_CONTAINS):
        return False
    # Path segments (Update\, CrashReporter\, mingw64\, …)
    try:
        parts = {p.lower() for p in path.parts}
    except OSError:
        parts = set()
    if parts & _SKIP_PATH_PARTS:
        return False
    if any(_looks_like_uninstall(part) for part in parts):
        return False
    return True


def should_skip_dir(name: str) -> bool:
    return name.lower() in _SKIP_DIR_NAMES


def should_skip_product_folder(name: str) -> bool:
    return name.lower() in _SKIP_PRODUCT_FOLDER_NAMES


def _name_match_rank(stem: str, preferred: str) -> int | None:
    """Return match quality (lower is better) or None if too weak."""
    if not stem or not preferred:
        return None
    if stem == preferred or compact_name(stem) == compact_name(preferred):
        return 0
    tokens = [t for t in preferred.split("_") if t]
    if len(stem) >= 4 and stem in tokens:
        return 1
    if len(stem) >= 4 and preferred.startswith(stem):
        return 2
    stem_c = compact_name(stem)
    pref_c = compact_name(preferred)
    # Folder "Docker" matches "Docker Desktop"; folder "MongoDB" matches mongod.
    if len(pref_c) >= 4 and pref_c in stem_c:
        return 3
    if len(stem_c) >= 4 and stem_c in pref_c:
        return 4
    return None


def _path_priority_penalty(path: Path) -> int:
    """Higher = worse (cli bin/, resources/, versioned Chromium copies, …)."""
    try:
        parts = [p.lower() for p in path.parts]
    except OSError:
        return 99
    penalty = sum(1 for p in parts if p in _LOW_PRIORITY_PATH_PARTS)
    # Version-looking folder: 1.2.3 or 120.0.6099.109
    for part in parts:
        if re.fullmatch(r"\d+(?:\.\d+){1,4}", part):
            penalty += 2
            break
    return penalty


def _strip_version_suffix(name: str) -> str:
    """datagrip_2026_1_2 → datagrip; keeps meaningful product tokens."""
    cleaned = re.sub(r"(?:_?\d+(?:\.\d+)*)+$", "", name).strip("_")
    return cleaned or name


def find_primary_exe(
    app_dir: Path,
    *,
    max_depth: int = 3,
    preferred: str | None = None,
) -> Path | None:
    """Prefer a launchable .exe whose name matches the app folder / display name."""
    preferred_key = normalize_name(preferred) if preferred else normalize_name(app_dir.name)
    preferred_keys = [preferred_key]
    stripped = _strip_version_suffix(preferred_key)
    if stripped != preferred_key:
        preferred_keys.append(stripped)
    scored: list[tuple[int, int, int, str, Path]] = []

    for depth in range(max_depth + 1):
        pattern = "/".join(["*"] * depth + ["*.exe"]) if depth else "*.exe"
        try:
            matches = app_dir.glob(pattern)
        except OSError:
            continue
        for exe in matches:
            try:
                if not exe.is_file() or not _is_usable_exe(exe):
                    continue
            except OSError:
                continue
            stem_n = normalize_name(exe.stem)
            rank: int | None = None
            for key in preferred_keys:
                r = _name_match_rank(stem_n, key)
                if r is not None and (rank is None or r < rank):
                    rank = r
            # Soft: product token prefix (datagrip64↔datagrip, idea64↔intellij_idea)
            if rank is None:
                stem_c = compact_name(stem_n)
                for key in preferred_keys:
                    key_c = compact_name(key)
                    if len(key_c) >= 4 and (
                        stem_c.startswith(key_c) or key_c.startswith(stem_c)
                    ):
                        rank = 5
                        break
                    for token in key.split("_"):
                        tc = token.replace("_", "")
                        if len(tc) >= 4 and (
                            stem_c.startswith(tc) or tc.startswith(stem_c)
                        ):
                            rank = 5
                            break
                    if rank is not None:
                        break
            if rank is None:
                continue
            try:
                rel_depth = len(exe.relative_to(app_dir).parts)
            except ValueError:
                rel_depth = depth
            penalty = _path_priority_penalty(exe)
            scored.append((penalty, rank, rel_depth, str(exe).lower(), exe))

    if not scored:
        return None
    # Prefer real GUI paths over bin/resources CLI copies, then name match.
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return scored[0][4]


def _entry_quality(entry: dict[str, Any], *, source_rank: int) -> tuple:
    """Lower tuple wins when choosing metadata for the same launch target."""
    path_s = str(entry.get("path") or "")
    path = Path(path_s) if path_s else Path(".")
    return (
        source_rank,
        0 if entry.get("link") else 1,
        0 if entry.get("aumid") else 1,
        _path_priority_penalty(path) if path_s else 0,
        len(path.parts) if path_s else 0,
        (path_s or str(entry.get("aumid") or "")).lower(),
    )


def collect_usable_exes(
    root: Path,
    *,
    max_depth: int = DEFAULT_MAX_EXE_DEPTH,
    max_exes: int = DEFAULT_MAX_EXES,
    warnings: list[str] | None = None,
    remaining: list[int] | None = None,
) -> list[Path]:
    """Depth-limited scandir walk collecting usable .exe files under root."""
    warn = warnings if warnings is not None else []
    budget = remaining if remaining is not None else [max_exes]
    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        if budget[0] <= 0:
            return
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            warn.append(f"Inaccessible: {current} ({exc})")
            return

        for entry in entries:
            if budget[0] <= 0:
                return
            try:
                if entry.is_file(follow_symlinks=False):
                    if entry.name.lower().endswith(".exe"):
                        path = Path(entry.path)
                        if _is_usable_exe(path):
                            found.append(path)
                            budget[0] -= 1
                elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                    if should_skip_dir(entry.name):
                        continue
                    walk(Path(entry.path), depth + 1)
            except OSError as exc:
                warn.append(f"Inaccessible: {entry.path} ({exc})")

    try:
        if root.is_file() and _is_usable_exe(root):
            found.append(root)
            budget[0] -= 1
            return found
        if root.is_dir():
            walk(root, 0)
    except OSError as exc:
        warn.append(f"Inaccessible: {root} ({exc})")
    return found


def resolve_shortcut_target(lnk: Path, *, warnings: list[str] | None = None) -> Path | None:
    """Resolve a Windows .lnk target via in-process MS-SHLLINK parse (no PowerShell)."""
    warn = warnings if warnings is not None else []
    if sys.platform != "win32":
        return None
    try:
        from desktop_ai_agent.core.folder_index.shortcuts import resolve_lnk_target

        raw = resolve_lnk_target(lnk)
        if not raw:
            return None
        path = Path(os.path.expandvars(raw.strip().strip('"')))
        if path.suffix.lower() == ".exe" and path.is_file() and _is_usable_exe(path):
            return path
        return None
    except OSError as exc:
        warn.append(f"Start Menu shortcut unresolved: {lnk.name} ({exc})")
        return None


def collect_shortcut_launches(
    roots: Iterable[Path],
    *,
    max_exes: int,
    warnings: list[str] | None = None,
    remaining: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Resolve Start Menu / Startup .lnk shortcuts.

    Each hit keeps the shortcut path in ``link`` so the index JSON can show
    both the .lnk and the target .exe.
    """
    warn = warnings if warnings is not None else []
    budget = remaining if remaining is not None else [max_exes]
    found: list[dict[str, Any]] = []

    lnks: list[tuple[Path, Path]] = []
    for root in roots:
        try:
            for lnk in root.rglob("*.lnk"):
                if _looks_like_uninstall(lnk.stem) or _looks_like_uninstall(lnk.name):
                    continue
                lnks.append((lnk, root))
        except OSError as exc:
            warn.append(f"Inaccessible: {root} ({exc})")

    for lnk, root in lnks:
        if budget[0] <= 0:
            break
        target = resolve_shortcut_target(lnk, warnings=warn)
        if target is None:
            continue
        found.append(
            _entry_for_exe(
                target,
                root=root,
                display=lnk.stem,
                link=norm_path(lnk),
                source="start_menu",
            )
        )
        budget[0] -= 1
    return found


def collect_start_menu_exes(
    roots: Iterable[Path],
    *,
    max_exes: int,
    warnings: list[str] | None = None,
    remaining: list[int] | None = None,
) -> list[Path]:
    """Compatibility wrapper: exe targets only."""
    return [
        Path(e["path"])
        for e in collect_shortcut_launches(
            roots, max_exes=max_exes, warnings=warnings, remaining=remaining
        )
    ]


def _strip_icon_index(raw: str) -> str:
    """`C:\\...\\app.exe,0` or quoted paths → plain filesystem path text."""
    text = os.path.expandvars(str(raw).strip().strip('"'))
    if "," in text:
        head, _, tail = text.rpartition(",")
        if tail.strip().lstrip("-").isdigit():
            text = head.strip().strip('"')
    return text


def _parse_display_icon(raw: str | None) -> Path | None:
    """DisplayIcon is often `C:\\...\\app.exe,0` — return the .exe if usable."""
    if not raw:
        return None
    path = Path(_strip_icon_index(raw))
    try:
        if path.suffix.lower() == ".exe" and path.is_file() and _is_usable_exe(path):
            return path
    except OSError:
        return None
    return None


def _sibling_exe_candidates(path: Path) -> list[Path]:
    """If DisplayIcon points at missing studio.exe, try studio64.exe beside it."""
    try:
        parent = path.parent
        if not parent.is_dir():
            return []
        stem = path.stem.lower()
        found: list[Path] = []
        with os.scandir(parent) as it:
            for entry in it:
                if not entry.name.lower().endswith(".exe"):
                    continue
                name = entry.name.lower()
                if name.startswith(stem) or stem.startswith(Path(entry.name).stem.lower()):
                    cand = Path(entry.path)
                    if _is_usable_exe(cand):
                        found.append(cand)
        return found
    except OSError:
        return []


def resolve_installed_app_path(
    *,
    display_name: str,
    display_icon: str | None = None,
    install_location: str | None = None,
) -> Path | None:
    """
    Resolve a Settings → Apps entry to a launchable .exe path.

    Order: DisplayIcon file → sibling exes next to a stale icon → InstallLocation
    primary match using the display name.
    """
    preferred = display_name.strip()

    if display_icon:
        icon_path = Path(_strip_icon_index(display_icon))
        direct = _parse_display_icon(display_icon)
        if direct is not None:
            return direct
        # Icon path missing/stale (studio.exe → studio64.exe) or .ico/.dll.
        try:
            search_dirs: list[Path] = []
            if icon_path.suffix.lower() == ".exe":
                search_dirs.append(icon_path.parent)
                siblings = _sibling_exe_candidates(icon_path)
                if siblings:
                    # Prefer name match to display / icon stem.
                    ranked = sorted(
                        siblings,
                        key=lambda p: (
                            0
                            if compact_name(p.stem) == compact_name(icon_path.stem)
                            else 1,
                            _path_priority_penalty(p),
                            str(p).lower(),
                        ),
                    )
                    return ranked[0]
            elif icon_path.suffix.lower() in {".ico", ".dll", ".png"}:
                search_dirs.append(icon_path.parent)
            for directory in search_dirs:
                if directory.is_dir():
                    hit = find_primary_exe(
                        directory, max_depth=2, preferred=preferred or icon_path.stem
                    )
                    if hit is not None:
                        return hit
                    # One level up (…\bin → product root).
                    parent = directory.parent
                    if parent.is_dir():
                        hit = find_primary_exe(
                            parent, max_depth=3, preferred=preferred or icon_path.stem
                        )
                        if hit is not None:
                            return hit
        except OSError:
            pass

    if install_location and str(install_location).strip():
        loc = Path(os.path.expandvars(str(install_location).strip().strip('"')))
        try:
            if loc.is_file() and _is_usable_exe(loc):
                return loc
            if loc.is_dir():
                hit = find_primary_exe(loc, max_depth=3, preferred=preferred)
                if hit is not None:
                    return hit
                # Last resort: shallowest usable exe under the install dir.
                shallow = collect_usable_exes(loc, max_depth=2, max_exes=30)
                if shallow:
                    shallow.sort(
                        key=lambda p: (
                            _path_priority_penalty(p),
                            len(p.parts),
                            str(p).lower(),
                        )
                    )
                    return shallow[0]
        except OSError:
            return None
    return None


_SKIP_INSTALLED_NAME_PREFIXES = (
    "microsoft visual c++",
    "microsoft .net",
    "microsoft windows desktop runtime",
    "microsoft asp.net",
    "microsoft.net",
    "update for",
    "security update",
    "definition update",
)


def _skip_installed_display_name(name: str) -> bool:
    lower = name.strip().lower()
    if not lower:
        return True
    # Settings → Apps sometimes lists "Uninstall Foo" rows — never keep them.
    if _looks_like_uninstall(lower):
        return True
    if re.match(r"kb\d", lower):
        return True
    return any(lower.startswith(p) for p in _SKIP_INSTALLED_NAME_PREFIXES)


def collect_installed_apps(
    *,
    max_exes: int = DEFAULT_MAX_EXES,
    warnings: list[str] | None = None,
    remaining: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Apps listed under Windows Settings → Apps (Uninstall registry).

    Every kept entry has DisplayName + a resolved launchable ``path``.
    """
    warn = warnings if warnings is not None else []
    if sys.platform != "win32":
        return []

    try:
        import winreg
    except ImportError:
        warn.append("winreg unavailable — installed apps index skipped")
        return []

    budget = remaining if remaining is not None else [max_exes]
    found: list[dict[str, Any]] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )

    def _reg_get(key: Any, value: str) -> Any:
        try:
            return winreg.QueryValueEx(key, value)[0]
        except OSError:
            return None

    for hive, subpath in roots:
        if budget[0] <= 0:
            break
        try:
            root_key = winreg.OpenKey(hive, subpath)
        except OSError as exc:
            warn.append(f"Installed apps registry inaccessible: {subpath} ({exc})")
            continue
        index = 0
        while budget[0] > 0:
            try:
                child_name = winreg.EnumKey(root_key, index)
                index += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(root_key, child_name)
            except OSError:
                continue
            try:
                if int(_reg_get(sub, "SystemComponent") or 0) == 1:
                    continue
                if _reg_get(sub, "ParentKeyName"):
                    continue
                display_name = _reg_get(sub, "DisplayName")
                if not isinstance(display_name, str) or _skip_installed_display_name(
                    display_name
                ):
                    continue
                icon_raw = _reg_get(sub, "DisplayIcon")
                loc_raw = _reg_get(sub, "InstallLocation")
                exe = resolve_installed_app_path(
                    display_name=display_name.strip(),
                    display_icon=icon_raw if isinstance(icon_raw, str) else None,
                    install_location=loc_raw if isinstance(loc_raw, str) else None,
                )
                if exe is None:
                    continue
                entry = _entry_for_exe(
                    exe,
                    root=exe.parent,
                    display=display_name.strip(),
                    source="settings",
                )
                found.append(entry)
                budget[0] -= 1
            finally:
                try:
                    sub.Close()
                except OSError:
                    pass
        try:
            root_key.Close()
        except OSError:
            pass

    return found


def _entry_for_exe(
    exe: Path,
    *,
    root: Path | None = None,
    display: str | None = None,
    link: str | None = None,
    source: str = "",
    extra_aliases: Iterable[str] = (),
) -> dict[str, Any]:
    folder = exe.parent
    label = (display or exe.stem).strip() or exe.stem
    name = normalize_name(label)
    aliases = _merge_aliases(
        [],
        name,
        normalize_name(exe.stem),
        compact_name(label),
        compact_name(exe.stem),
        *[normalize_name(a) for a in extra_aliases if a],
        *[compact_name(a) for a in extra_aliases if a],
    )
    entry: dict[str, Any] = {
        "name": name,
        "display": label,
        "path": norm_path(exe),
        "folder": norm_path(folder),
        "root": norm_path(root) if root is not None else norm_path(folder),
        "link": link or "",
        "source": source or "",
        "aumid": "",
        "aliases": aliases,
    }
    return entry


def _merge_aliases(existing: Iterable[str], *candidates: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in list(existing) + list(candidates):
        key = (raw or "").strip().lower()
        if not key or key == "unknown" or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _target_key(entry: dict[str, Any]) -> str:
    """Stable identity for a launch target (exe path or AUMID)."""
    aumid = str(entry.get("aumid") or "").strip().lower()
    if aumid:
        return f"aumid:{aumid}"
    return f"path:{str(entry.get('path') or '').strip().lower()}"


# Vendor umbrella folders: index each product subfolder, not one primary for the vendor.
_VENDOR_UMBRELLA_FOLDERS = frozenset(
    {
        "jetbrains",
        "adobe",
        "microsoft office",
        "microsoft visual studio",
        "google",
        "mozilla firefox",
        "oracle",
        "nvidia corporation",
    }
)

# Well-known Office launchers under Microsoft Office trees.
_OFFICE_LAUNCHERS = (
    "WINWORD.EXE",
    "EXCEL.EXE",
    "POWERPNT.EXE",
    "OUTLOOK.EXE",
    "ONENOTE.EXE",
    "MSPUB.EXE",
    "MSACCESS.EXE",
)


def collect_program_folder_launchers(
    product_dir: Path,
    *,
    max_depth: int = 3,
) -> list[Path]:
    """
    Launchers for one Program Files child.

    Normal folders → one primary exe.
    Vendor umbrellas (JetBrains/Adobe/Office/…) → primary per product subfolder
    plus known Office binaries when present.
    """
    found: list[Path] = []
    name_l = product_dir.name.lower()
    umbrella = name_l in _VENDOR_UMBRELLA_FOLDERS or name_l.startswith("adobe ")

    if umbrella:
        try:
            with os.scandir(product_dir) as it:
                children = list(it)
        except OSError:
            children = []
        for entry in children:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if should_skip_dir(entry.name):
                continue
            sub = Path(entry.path)
            primary = find_primary_exe(
                sub, max_depth=min(3, max_depth), preferred=entry.name
            )
            if primary is not None:
                found.append(primary)
        # Office often nests WINWORD under root\\Office16 without a nice product folder.
        if "office" in name_l:
            for depth in range(min(4, max_depth) + 1):
                pattern = "/".join(["*"] * depth + ["*.exe"]) if depth else "*.exe"
                try:
                    for exe in product_dir.glob(pattern):
                        if exe.name.upper() in _OFFICE_LAUNCHERS and _is_usable_exe(exe):
                            found.append(exe)
                except OSError:
                    continue
    else:
        primary = find_primary_exe(product_dir, max_depth=min(3, max_depth))
        if primary is not None:
            found.append(primary)

    # Dedupe paths while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for exe in found:
        key = norm_path(exe).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(exe)
    return unique


# Source preference when the same *target* appears from multiple sources.
_SOURCE_START_MENU = 0
_SOURCE_INSTALLED = 1
_SOURCE_APPX = 2
_SOURCE_GAMES = 3
_SOURCE_PROGRAM_FILES = 4
_SOURCE_EXTRA = 5


def index_executables(
    *,
    drives: tuple[str, ...] = DRIVES,
    include_start_menu: bool = True,
    include_installed_apps: bool = True,
    include_appx: bool = True,
    include_games_folders: bool = True,
    extra_roots: Iterable[str | Path] = (),
    max_exe_depth: int = DEFAULT_MAX_EXE_DEPTH,
    max_exes: int = DEFAULT_MAX_EXES,
) -> IndexResult:
    """
    Index real application launchers.

    Identity is the launch *target* (exe path or AUMID). Extra display names
    become ``aliases`` instead of wiping other apps that share a compact name.
    """
    warnings: list[str] = []
    present = tuple(existing_drives(drives, warnings=warnings))
    # target_key -> (quality, entry)
    by_target: dict[str, tuple[tuple, dict[str, Any]]] = {}
    remaining = [max(1, int(max_exes))]

    def add_entries(
        entries: Iterable[dict[str, Any]],
        *,
        source_rank: int,
    ) -> None:
        for entry in entries:
            labels = (
                entry.get("name") or "",
                entry.get("display") or "",
                entry.get("path") or "",
                entry.get("link") or "",
                entry.get("aumid") or "",
            )
            if any(_looks_like_uninstall(str(x)) for x in labels):
                continue

            aumid = str(entry.get("aumid") or "").strip()
            path_s = str(entry.get("path") or "").strip()
            if aumid:
                # Store apps: path may be empty.
                pass
            else:
                if not path_s:
                    continue
                try:
                    if not _is_usable_exe(Path(path_s)):
                        continue
                except (OSError, TypeError):
                    continue

            name_key = compact_name(entry.get("name") or "")
            if not name_key or name_key == "unknown":
                continue

            entry = {
                **entry,
                "link": entry.get("link") or "",
                "source": entry.get("source") or "",
                "aumid": aumid,
                "aliases": _merge_aliases(
                    entry.get("aliases") or [],
                    entry.get("name") or "",
                    compact_name(entry.get("name") or ""),
                    normalize_name(entry.get("display") or ""),
                    compact_name(entry.get("display") or ""),
                ),
            }
            key = _target_key(entry)
            quality = _entry_quality(entry, source_rank=source_rank)
            prev = by_target.get(key)
            if prev is None:
                by_target[key] = (quality, entry)
                continue

            prev_q, prev_e = prev
            # Same target again: keep best metadata, always merge aliases.
            merged_aliases = _merge_aliases(
                prev_e.get("aliases") or [],
                *(entry.get("aliases") or []),
                entry.get("name") or "",
                prev_e.get("name") or "",
            )
            if quality < prev_q:
                entry = {**entry, "aliases": merged_aliases}
                # Prefer keeping a link if the winner lacked one.
                if not entry.get("link") and prev_e.get("link"):
                    entry["link"] = prev_e["link"]
                by_target[key] = (quality, entry)
            else:
                prev_e = {**prev_e, "aliases": merged_aliases}
                if not prev_e.get("link") and entry.get("link"):
                    prev_e["link"] = entry["link"]
                by_target[key] = (prev_q, prev_e)

    def add_exes(
        exes: Iterable[Path],
        *,
        root: Path | None = None,
        source_rank: int,
        source: str = "",
    ) -> None:
        add_entries(
            [
                _entry_for_exe(exe, root=root, source=source)
                for exe in exes
            ],
            source_rank=source_rank,
        )

    if include_start_menu:
        sm_roots = discover_start_menu_roots(warnings=warnings)
        add_entries(
            collect_shortcut_launches(
                sm_roots,
                max_exes=remaining[0],
                warnings=warnings,
                remaining=remaining,
            ),
            source_rank=_SOURCE_START_MENU,
        )

    if include_installed_apps:
        add_entries(
            collect_installed_apps(
                max_exes=remaining[0],
                warnings=warnings,
                remaining=remaining,
            ),
            source_rank=_SOURCE_INSTALLED,
        )

    if include_appx:
        from desktop_ai_agent.core.folder_index.appx import collect_appx_launches

        add_entries(
            collect_appx_launches(max_apps=remaining[0], warnings=warnings),
            source_rank=_SOURCE_APPX,
        )

    if include_games_folders:
        for games_root in discover_games_roots(
            present, warnings=warnings, check_drives=False
        ):
            if remaining[0] <= 0:
                break
            add_exes(
                collect_usable_exes(
                    games_root,
                    max_depth=max_exe_depth,
                    max_exes=remaining[0],
                    warnings=warnings,
                    remaining=remaining,
                ),
                root=games_root,
                source_rank=_SOURCE_GAMES,
                source="games",
            )

    # Program Files: primary per product; umbrellas expand to sub-products.
    for pf_root in discover_program_roots(
        present, warnings=warnings, check_drives=False
    ):
        try:
            with os.scandir(pf_root) as it:
                children = list(it)
        except OSError as exc:
            warnings.append(f"Inaccessible: {pf_root} ({exc})")
            continue
        for entry in children:
            if remaining[0] <= 0:
                break
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError as exc:
                warnings.append(f"Inaccessible: {entry.path} ({exc})")
                continue
            if should_skip_product_folder(entry.name) or should_skip_dir(entry.name):
                continue
            child = Path(entry.path)
            launchers = collect_program_folder_launchers(
                child, max_depth=min(4, max_exe_depth)
            )
            if launchers:
                add_exes(
                    launchers,
                    root=pf_root,
                    source_rank=_SOURCE_PROGRAM_FILES,
                    source="program_files",
                )

    for raw in extra_roots:
        if remaining[0] <= 0:
            break
        path = Path(raw)
        try:
            if not path.exists():
                warnings.append(f"Extra root missing: {path}")
                continue
        except OSError as exc:
            warnings.append(f"Inaccessible: {path} ({exc})")
            continue
        add_exes(
            collect_usable_exes(
                path,
                max_depth=max_exe_depth,
                max_exes=remaining[0],
                warnings=warnings,
                remaining=remaining,
            ),
            root=path if path.is_dir() else path.parent,
            source_rank=_SOURCE_EXTRA,
            source="extra",
        )

    if remaining[0] <= 0:
        warnings.append(f"Exe index stopped early: max_exes={max_exes} reached")

    entries = sorted(
        (item[1] for item in by_target.values()),
        key=lambda e: (e["name"], (e.get("path") or e.get("aumid") or "").lower()),
    )
    return IndexResult(entries=entries, warnings=warnings)


def index_program_files(
    *,
    drives: tuple[str, ...] = DRIVES,
    include_start_menu: bool = False,
    include_installed_apps: bool = False,
    include_appx: bool = False,
    include_games_folders: bool = False,
    extra_roots: Iterable[str | Path] = (),
    max_exe_depth: int = DEFAULT_MAX_EXE_DEPTH,
    max_exes: int = DEFAULT_MAX_EXES,
) -> list[dict[str, Any]]:
    """
    Return executable entries only.

    Default keeps Start Menu / Games / installed-apps / AppX off so unit tests
    that only fake Program Files roots stay focused; SessionSetup enables the
    full scan via index_executables().
    """
    return index_executables(
        drives=drives,
        include_start_menu=include_start_menu,
        include_installed_apps=include_installed_apps,
        include_appx=include_appx,
        include_games_folders=include_games_folders,
        extra_roots=extra_roots,
        max_exe_depth=max_exe_depth,
        max_exes=max_exes,
    ).entries


def format_index_lines(entries: list[dict[str, Any]]) -> str:
    """Human/agent readable lines: `name, (exe|aumid)[, link=][, source=]`."""
    lines: list[str] = []
    for e in entries:
        target = e.get("path") or e.get("aumid") or ""
        line = f"{e['name']}, ({target})"
        link = e.get("link") or ""
        if link:
            line += f", link=({link})"
        source = e.get("source") or ""
        if source:
            line += f", source={source}"
        aliases = e.get("aliases") or []
        if aliases:
            extra = [a for a in aliases if a != e.get("name")]
            if extra:
                line += f", aliases=[{', '.join(extra[:6])}]"
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")
