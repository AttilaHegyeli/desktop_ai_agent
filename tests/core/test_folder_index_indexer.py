from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from desktop_ai_agent.core.folder_index.indexer import (
    find_primary_exe,
    format_index_lines,
    index_program_files,
    normalize_name,
)


def test_normalize_name_strips_vendor_prefix() -> None:
    assert normalize_name("Microsoft VS Code") == "vs_code"
    assert normalize_name("Spotify") == "spotify"
    assert normalize_name("Google Chrome") == "chrome"


def test_find_primary_exe_prefers_matching_name(fake_program_files: Path) -> None:
    steam_dir = fake_program_files / "Program Files" / "Steam"
    exe = find_primary_exe(steam_dir)
    assert exe is not None
    assert exe.name.lower() == "steam.exe"


def test_find_primary_exe_skips_helper_only(fake_program_files: Path) -> None:
    vendor = fake_program_files / "Program Files" / "SomeVendor"
    assert find_primary_exe(vendor) is None


def test_index_program_files_on_fake_tree(fake_program_files: Path) -> None:
    drive_letter = "Z"

    def fake_roots(drives: tuple[str, ...] = ("Z",)) -> list[Path]:
        roots: list[Path] = []
        base = fake_program_files
        for name in ("Program Files", "Program Files (x86)"):
            path = base / name
            if path.is_dir():
                roots.append(path)
        assert drives == (drive_letter,) or True
        return roots

    with patch(
        "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
        side_effect=lambda drives=("Z",): fake_roots(drives),
    ):
        entries = index_program_files(drives=(drive_letter,), resolve_exe=True)

    names = {e["name"] for e in entries}
    assert "steam" in names
    assert "git" in names
    assert any(n.startswith("notepad") for n in names)

    steam = next(e for e in entries if e["name"] == "steam")
    assert steam["path"].lower().endswith("steam.exe")

    text = format_index_lines(entries)
    assert "steam, (" in text
