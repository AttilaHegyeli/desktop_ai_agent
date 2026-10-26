from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from desktop_ai_agent.core.folder_index.indexer import (
    compact_name,
    format_index_lines,
    index_executables,
    normalize_name,
)
from desktop_ai_agent.paths import PROGRAM_FILES_INDEX_JSON, PROGRAM_FILES_INDEX_TXT

logger = logging.getLogger(__name__)


def _folder_index_cfg(setup: dict[str, Any] | None) -> dict[str, Any]:
    if not setup:
        return {}
    if isinstance(setup.get("folder_index"), dict):
        return setup["folder_index"]
    legacy = setup.get("program_files_index")
    return legacy if isinstance(legacy, dict) else {}


def build_kwargs_from_setup(setup: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map config/setup.json folder_index block → ProgramCatalog.build kwargs."""
    if setup is None:
        from desktop_ai_agent.core.config_loader import load_setup

        setup = load_setup()
    cfg = _folder_index_cfg(setup)
    apps = cfg.get("apps") if isinstance(cfg.get("apps"), dict) else {}
    drives_raw = cfg.get("drives") or ["C", "D", "E"]
    drives = tuple(str(d).rstrip(":").upper() for d in drives_raw)
    return {
        "drives": drives,
        "include_start_menu": bool(apps.get("include_start_menu", True)),
        "include_installed_apps": bool(apps.get("include_installed_apps", True)),
        "include_appx": bool(apps.get("include_appx", True)),
        "include_games_folders": bool(apps.get("include_games_folders", True)),
        "extra_roots": list(apps.get("extra_roots") or []),
        "max_exe_depth": int(apps.get("max_exe_depth", 4)),
        "max_exes": int(apps.get("max_exes", 20_000)),
    }


def _alias_list(entry: dict[str, Any]) -> list[str]:
    aliases = list(entry.get("aliases") or [])
    aliases.append(entry.get("name") or "")
    aliases.append(normalize_name(entry.get("display", "") or ""))
    aliases.append(compact_name(entry.get("display", "") or ""))
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in aliases:
        key = (a or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _search_rank(query: str, entry: dict[str, Any]) -> int | None:
    """
    Lower is better. None = no match.

    0 exact / compact-equal (MongoDB Compass ↔ MongoDBCompass) incl. aliases
    1 prefix
    2 all query tokens appear in name/display/aliases
    3 substring in name/display/aliases
    4 path / aumid contains query
    """
    needle = normalize_name(query)
    if not needle:
        return None
    needle_c = compact_name(query)
    name = entry["name"]
    display = normalize_name(entry.get("display", "") or "")
    name_c = compact_name(name)
    display_c = compact_name(display) if display else ""
    aliases = _alias_list(entry)
    alias_c = [compact_name(a) for a in aliases]
    tokens = [t for t in needle.split("_") if len(t) >= 2]

    if needle == name or (needle_c and needle_c == name_c):
        return 0
    if needle_c and display_c and needle_c == display_c:
        return 0
    if needle in aliases or (needle_c and needle_c in alias_c):
        return 0

    if (
        name.startswith(needle)
        or display.startswith(needle)
        or (needle_c and (name_c.startswith(needle_c) or display_c.startswith(needle_c)))
        or any(a.startswith(needle) for a in aliases)
        or (needle_c and any(a.startswith(needle_c) for a in alias_c))
    ):
        return 1

    hay = " ".join([name_c, display_c, *alias_c])
    if tokens and all(t.replace("_", "") in hay or t in name or t in display for t in tokens):
        if sum(len(t) for t in tokens) >= 4 or len(tokens) >= 2:
            return 2

    if len(needle) >= 4 and (needle in name or needle in display or any(needle in a for a in aliases)):
        return 3
    if len(needle_c) >= 4 and (
        needle_c in name_c
        or (display_c and needle_c in display_c)
        or any(needle_c in a for a in alias_c)
    ):
        return 3

    path_l = (
        (entry.get("path") or entry.get("aumid") or "")
        .lower()
        .replace("\\", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )
    path_c = path_l.replace("_", "")
    if len(needle) >= 4 and needle in path_l:
        return 4
    if len(needle_c) >= 4 and needle_c in path_c:
        return 4
    return None


class ProgramCatalog:
    """In-memory + on-disk executable / AppX catalog for agent search."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])
        self.warnings: list[str] = []

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def build(
        cls,
        *,
        drives: tuple[str, ...] = ("C", "D", "E"),
        include_start_menu: bool = True,
        include_installed_apps: bool = True,
        include_appx: bool = True,
        include_games_folders: bool = True,
        extra_roots: list[str] | tuple[str, ...] = (),
        max_exe_depth: int = 4,
        max_exes: int = 20_000,
    ) -> ProgramCatalog:
        result = index_executables(
            drives=drives,
            include_start_menu=include_start_menu,
            include_installed_apps=include_installed_apps,
            include_appx=include_appx,
            include_games_folders=include_games_folders,
            extra_roots=extra_roots,
            max_exe_depth=max_exe_depth,
            max_exes=max_exes,
        )
        catalog = cls(result.entries)
        catalog.warnings = list(result.warnings)
        return catalog

    @classmethod
    def build_from_setup(cls, setup: dict[str, Any] | None = None) -> ProgramCatalog:
        """Build using config/setup.json (or provided setup dict)."""
        return cls.build(**build_kwargs_from_setup(setup))

    @classmethod
    def load(
        cls,
        json_path: Path | None = None,
        *,
        rebuild_if_missing: bool = True,
        setup: dict[str, Any] | None = None,
    ) -> ProgramCatalog:
        path = json_path or PROGRAM_FILES_INDEX_JSON
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(list(data.get("programs", [])))
            if rebuild_if_missing:
                catalog = cls.build_from_setup(setup)
                catalog.save(json_path=path)
                return catalog
        except FileNotFoundError:
            logger.warning("Program index missing at %s", path)
            if rebuild_if_missing:
                try:
                    catalog = cls.build_from_setup(setup)
                    catalog.save(json_path=path)
                    return catalog
                except Exception:
                    logger.exception("Failed to rebuild program index from setup.json")
                    return cls([])
            return cls([])
        except json.JSONDecodeError:
            logger.exception("Corrupt program index JSON at %s", path)
            return cls([])
        except OSError:
            logger.exception("Cannot read program index at %s", path)
            return cls([])
        return cls([])

    def save(
        self,
        *,
        json_path: Path | None = None,
        txt_path: Path | None = None,
    ) -> None:
        json_target = json_path or PROGRAM_FILES_INDEX_JSON
        txt_target = txt_path or PROGRAM_FILES_INDEX_TXT
        json_target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"count": len(self.entries), "programs": self.entries}
        json_target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        txt_target.write_text(format_index_lines(self.entries), encoding="utf-8")

    def as_text(self) -> str:
        return format_index_lines(self.entries)

    def list_all(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for e in self.entries:
            row = {"name": e["name"], "path": e.get("path") or ""}
            if e.get("display"):
                row["display"] = str(e["display"])
            if e.get("link"):
                row["link"] = str(e["link"])
            if e.get("source"):
                row["source"] = str(e["source"])
            if e.get("aumid"):
                row["aumid"] = str(e["aumid"])
            if e.get("aliases"):
                row["aliases"] = ",".join(str(a) for a in e["aliases"])
            rows.append(row)
        return rows

    def search(self, query: str, *, limit: int = 3) -> list[dict[str, str]]:
        """Return best matches only (default top 3). Empty list on no hit."""
        scored: list[tuple[int, int, dict[str, str]]] = []
        try:
            for entry in self.entries:
                rank = _search_rank(query, entry)
                if rank is None:
                    continue
                hit = {
                    "name": entry["name"],
                    "path": entry.get("path") or "",
                }
                if entry.get("link"):
                    hit["link"] = str(entry["link"])
                if entry.get("aumid"):
                    hit["aumid"] = str(entry["aumid"])
                if entry.get("aliases"):
                    hit["aliases"] = ",".join(str(a) for a in entry["aliases"])
                scored.append((rank, len(entry["name"]), hit))
        except Exception:
            logger.exception("Program catalog search failed for query=%r", query)
            return []

        if not scored:
            return []

        scored.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2]["name"],
                item[2].get("path") or item[2].get("aumid") or "",
            )
        )
        best_rank = scored[0][0]
        top_tier = [item for item in scored if item[0] == best_rank]
        return [item for _, _, item in top_tier[: max(1, limit)]]
