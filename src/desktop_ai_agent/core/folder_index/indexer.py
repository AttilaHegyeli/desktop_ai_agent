from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DRIVES = ("C", "D", "E")
PROGRAM_DIR_NAMES = ("Program Files", "Program Files (x86)")

_SKIP_EXE_PREFIXES = (
    "unins",
    "uninstall",
    "setup",
    "update",
    "installer",
    "crash",
    "helper",
)

_NAME_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(raw: str) -> str:
    """Turn 'Microsoft VS Code' / 'Spotify' into vs_code / spotify style keys."""
    cleaned = _NAME_RE.sub("_", raw.strip().lower()).strip("_")
    for prefix in ("microsoft_", "google_", "adobe_", "oracle_"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 2:
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned or "unknown"


def discover_program_roots(drives: tuple[str, ...] = DRIVES) -> list[Path]:
    roots: list[Path] = []
    for drive in drives:
        for folder in PROGRAM_DIR_NAMES:
            path = Path(f"{drive}:/") / folder
            if path.is_dir():
                roots.append(path)
    return roots


def _is_usable_exe(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".exe"):
        return False
    stem = path.stem.lower()
    return not any(stem.startswith(p) or p in stem for p in _SKIP_EXE_PREFIXES)


def _name_match_rank(stem: str, preferred: str) -> int | None:
    """Return match quality (lower is better) or None if too weak."""
    if not stem or not preferred:
        return None
    if stem == preferred:
        return 0
    tokens = preferred.split("_")
    if len(stem) >= 4 and stem in tokens:
        return 1
    # brave.exe under BraveSoftware — allow folder starting with exe stem
    # but never docker_ai.exe for folder Docker (stem longer than preferred)
    if len(stem) >= 4 and preferred.startswith(stem):
        return 2
    return None


def find_primary_exe(app_dir: Path, *, max_depth: int = 2) -> Path | None:
    """Prefer a launchable .exe whose name matches the app folder; else None."""
    preferred = normalize_name(app_dir.name)
    scored: list[tuple[int, int, str, Path]] = []

    for depth in range(max_depth + 1):
        pattern = "/".join(["*"] * depth + ["*.exe"]) if depth else "*.exe"
        for exe in app_dir.glob(pattern):
            if not exe.is_file() or not _is_usable_exe(exe):
                continue
            rank = _name_match_rank(normalize_name(exe.stem), preferred)
            if rank is None:
                continue
            rel_depth = len(exe.relative_to(app_dir).parts)
            scored.append((rank, rel_depth, str(exe).lower(), exe))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return scored[0][3]


def index_program_files(
    *,
    drives: tuple[str, ...] = DRIVES,
    resolve_exe: bool = True,
) -> list[dict[str, Any]]:
    """
    Index only Program Files / Program Files (x86) on C/D/E.

    Returns entries shaped for text export:
      name  -> vs_code
      path  -> install folder or primary .exe
    """
    seen: dict[str, dict[str, Any]] = {}

    for root in discover_program_roots(drives):
        try:
            children = list(root.iterdir())
        except OSError:
            continue

        for child in children:
            if not child.is_dir():
                continue
            name = normalize_name(child.name)
            path = child
            if resolve_exe:
                exe = find_primary_exe(child)
                if exe is not None:
                    path = exe

            entry = {
                "name": name,
                "display": child.name,
                "path": str(path),
                "folder": str(child),
                "root": str(root),
            }
            # Prefer an entry that already resolved to an .exe
            prior = seen.get(name)
            if prior is None or (
                prior["path"].lower().endswith(".exe") is False
                and entry["path"].lower().endswith(".exe")
            ):
                seen[name] = entry

    return sorted(seen.values(), key=lambda e: e["name"])


def format_index_lines(entries: list[dict[str, Any]]) -> str:
    """Human/agent readable lines: `vs_code, (C:\\...\\Code.exe)`."""
    lines = [f"{e['name']}, ({e['path']})" for e in entries]
    return "\n".join(lines) + ("\n" if lines else "")
