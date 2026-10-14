from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from desktop_ai_agent.core.folder_index.indexer import (
    format_index_lines,
    index_program_files,
    normalize_name,
)
from desktop_ai_agent.paths import PROGRAM_FILES_INDEX_JSON, PROGRAM_FILES_INDEX_TXT


class ProgramCatalog:
    """In-memory + on-disk Program Files catalog for agent search."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def build(cls, *, resolve_exe: bool = True) -> ProgramCatalog:
        return cls(index_program_files(resolve_exe=resolve_exe))

    @classmethod
    def load(
        cls,
        json_path: Path | None = None,
        *,
        rebuild_if_missing: bool = True,
    ) -> ProgramCatalog:
        path = json_path or PROGRAM_FILES_INDEX_JSON
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(list(data.get("programs", [])))
            if rebuild_if_missing:
                catalog = cls.build()
                catalog.save(json_path=path)
                return catalog
        except Exception:  # noqa: BLE001 — never break agent startup/search
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
        return [{"name": e["name"], "path": e["path"]} for e in self.entries]

    def search(self, query: str, *, limit: int = 3) -> list[dict[str, str]]:
        """Return best matches only (default top 3). Empty list on no hit."""
        try:
            needle = normalize_name(query)
            if not needle:
                return []

            scored: list[tuple[int, int, dict[str, str]]] = []
            for entry in self.entries:
                name = entry["name"]
                display = normalize_name(entry.get("display", ""))
                path_l = entry["path"].lower().replace("\\", "_").replace(" ", "_")
                if needle == name:
                    rank = 0
                elif name.startswith(needle) or display.startswith(needle):
                    rank = 1
                elif needle in name or needle in display:
                    rank = 2
                elif needle in path_l:
                    rank = 3
                else:
                    continue
                # Prefer shorter names (more specific) at same rank
                scored.append((rank, len(name), {"name": name, "path": entry["path"]}))

            scored.sort(key=lambda item: (item[0], item[1], item[2]["name"]))
            return [item for _, _, item in scored[: max(1, limit)]]
        except Exception:  # noqa: BLE001
            return []
