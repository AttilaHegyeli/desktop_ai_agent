from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from desktop_ai_agent.core.folder_index.indexer import (
    _is_usable_exe,
    compact_name,
    find_primary_exe,
    format_index_lines,
    index_executables,
    index_program_files,
    normalize_name,
)


def test_normalize_name_strips_vendor_prefix() -> None:
    assert normalize_name("Microsoft VS Code") == "vs_code"
    assert normalize_name("Spotify") == "spotify"
    assert normalize_name("Google Chrome") == "chrome"


def test_compact_name_drops_separators() -> None:
    assert compact_name("MongoDB Compass") == "mongodbcompass"
    assert compact_name("MongoDBCompass") == "mongodbcompass"


def test_is_usable_exe_filters_junk(tmp_path: Path) -> None:
    good = tmp_path / "MongoDBCompass.exe"
    good.write_bytes(b"MZ")
    assert _is_usable_exe(good)

    setup = tmp_path / "AacSetup_A.exe"
    setup.write_bytes(b"MZ")
    assert not _is_usable_exe(setup)

    crash = tmp_path / "BraveCrashHandler.exe"
    crash.write_bytes(b"MZ")
    assert not _is_usable_exe(crash)

    for junk in ("uninstall.exe", "Uninstall Node.js.exe", "unins000.exe", "Foo_uninstall.exe"):
        path = tmp_path / junk
        path.write_bytes(b"MZ")
        assert not _is_usable_exe(path), junk

    mingw = tmp_path / "mingw64" / "bin"
    mingw.mkdir(parents=True)
    tool = mingw / "od.exe"
    tool.write_bytes(b"MZ")
    assert not _is_usable_exe(tool)


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

    def fake_roots(drives: tuple[str, ...] = ("Z",), **_kwargs) -> list[Path]:
        roots: list[Path] = []
        base = fake_program_files
        for name in ("Program Files", "Program Files (x86)"):
            path = base / name
            if path.is_dir():
                roots.append(path)
        return roots

    with (
        patch(
            "desktop_ai_agent.core.folder_index.indexer.existing_drives",
            return_value=[drive_letter],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
            side_effect=fake_roots,
        ),
    ):
        entries = index_program_files(drives=(drive_letter,))

    names = {e["name"] for e in entries}
    assert "steam" in names
    assert "git" in names
    assert any(n.startswith("notepad") for n in names)
    # helper-only folder must not contribute an entry
    assert "helper" not in names
    assert "somevendor" not in names

    steam = next(e for e in entries if e["name"] == "steam")
    assert steam["path"].lower().endswith("steam.exe")

    text = format_index_lines(entries)
    assert "steam, (" in text


def test_program_files_keeps_one_primary_per_product(
    fake_program_files: Path,
) -> None:
    steam = fake_program_files / "Program Files" / "Steam"
    (steam / "steam_launcher.exe").write_bytes(b"MZ")

    def fake_roots(drives: tuple[str, ...] = ("Z",), **_kwargs) -> list[Path]:
        return [fake_program_files / "Program Files"]

    with (
        patch(
            "desktop_ai_agent.core.folder_index.indexer.existing_drives",
            return_value=["Z"],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
            side_effect=fake_roots,
        ),
    ):
        result = index_executables(
            drives=("Z",),
            include_start_menu=False,
            include_installed_apps=False,
            include_appx=False,
            include_games_folders=False,
        )

    steam_entries = [e for e in result.entries if e["name"] == "steam"]
    assert len(steam_entries) == 1
    assert steam_entries[0]["path"].lower().endswith("steam.exe")
    assert not any(e["name"] == "steam_launcher" for e in result.entries)


def test_unique_name_prefers_shallow_gui_path(tmp_path: Path) -> None:
    """Multiple Docker Desktop.exe copies → one catalog row, best path."""
    docker = tmp_path / "Program Files" / "Docker"
    main = docker / "Docker" / "Docker Desktop.exe"
    nested = docker / "Docker" / "resources" / "cli-plugins" / "Docker Desktop.exe"
    main.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    main.write_bytes(b"MZ")
    nested.write_bytes(b"MZ")

    def fake_roots(drives: tuple[str, ...] = ("Z",), **_kwargs) -> list[Path]:
        return [tmp_path / "Program Files"]

    with (
        patch(
            "desktop_ai_agent.core.folder_index.indexer.existing_drives",
            return_value=["Z"],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
            side_effect=fake_roots,
        ),
    ):
        result = index_executables(
            drives=("Z",),
            include_start_menu=False,
            include_installed_apps=False,
            include_appx=False,
            include_games_folders=False,
        )

    desktop = [e for e in result.entries if "docker" in e["name"]]
    assert len(desktop) == 1
    assert desktop[0]["path"].lower().endswith("docker desktop.exe")
    assert "cli-plugins" not in desktop[0]["path"].lower()
    assert "resources" not in desktop[0]["path"].lower()


def test_shortcut_launch_keeps_link_in_entry(tmp_path: Path) -> None:
    from desktop_ai_agent.core.folder_index.indexer import collect_shortcut_launches

    exe = tmp_path / "Docker Desktop.exe"
    exe.write_bytes(b"MZ")
    lnk = tmp_path / "Docker Desktop.lnk"
    lnk.write_text("dummy")

    with patch(
        "desktop_ai_agent.core.folder_index.indexer.resolve_shortcut_target",
        return_value=exe,
    ):
        hits = collect_shortcut_launches([tmp_path], max_exes=10)

    assert len(hits) == 1
    assert hits[0]["name"] == "docker_desktop"
    assert hits[0]["path"].lower().endswith("docker desktop.exe")
    assert hits[0]["link"].lower().endswith("docker desktop.lnk")


def test_parse_display_icon_strips_index() -> None:
    from desktop_ai_agent.core.folder_index.indexer import _parse_display_icon

    assert _parse_display_icon(None) is None
    # Missing file → None (do not invent paths)
    assert _parse_display_icon(r"C:\missing\app.exe,0") is None


def test_resolve_installed_app_path_uses_sibling_exe(tmp_path: Path) -> None:
    from desktop_ai_agent.core.folder_index.indexer import resolve_installed_app_path

    bin_dir = tmp_path / "Android Studio" / "bin"
    bin_dir.mkdir(parents=True)
    real = bin_dir / "studio64.exe"
    real.write_bytes(b"MZ")
    stale_icon = str(bin_dir / "studio.exe") + ",0"

    hit = resolve_installed_app_path(
        display_name="Android Studio",
        display_icon=stale_icon,
        install_location=None,
    )
    assert hit is not None
    assert hit.name.lower() == "studio64.exe"


def test_resolve_installed_app_path_from_install_location(tmp_path: Path) -> None:
    from desktop_ai_agent.core.folder_index.indexer import resolve_installed_app_path

    root = tmp_path / "VLC"
    root.mkdir()
    exe = root / "vlc.exe"
    exe.write_bytes(b"MZ")

    hit = resolve_installed_app_path(
        display_name="VLC media player",
        display_icon=None,
        install_location=str(root),
    )
    assert hit is not None
    assert hit.name.lower() == "vlc.exe"


def test_skip_installed_runtime_names() -> None:
    from desktop_ai_agent.core.folder_index.indexer import _skip_installed_display_name

    assert _skip_installed_display_name("Microsoft Visual C++ 2015 Redistributable")
    assert _skip_installed_display_name("Microsoft .NET Runtime")
    assert _skip_installed_display_name("Uninstall Node.js")
    assert _skip_installed_display_name("Uninstall Rome - Total War")
    assert not _skip_installed_display_name("Docker Desktop")


def test_games_folder_portable_exe(tmp_path: Path) -> None:
    drive = tmp_path / "D"
    games = drive / "Users" / "player" / "games"
    games.mkdir(parents=True)
    exe = games / "stronghold crusader.exe"
    exe.write_bytes(b"MZ")

    def fake_games(drives: tuple[str, ...] = ("D",), **_kwargs) -> list[Path]:
        return [games]

    with (
        patch(
            "desktop_ai_agent.core.folder_index.indexer.existing_drives",
            return_value=["D"],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
            return_value=[],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_games_roots",
            side_effect=fake_games,
        ),
    ):
        result = index_executables(
            drives=("D",),
            include_start_menu=False,
            include_installed_apps=False,
            include_appx=False,
            include_games_folders=True,
        )

    names = {e["name"] for e in result.entries}
    assert "stronghold_crusader" in names
    hit = next(e for e in result.entries if e["name"] == "stronghold_crusader")
    assert hit["path"].lower().endswith("stronghold crusader.exe")


def test_missing_drive_warning() -> None:
    from desktop_ai_agent.core.folder_index.indexer import existing_drives

    warnings: list[str] = []
    found = existing_drives(("Q",), warnings=warnings)
    if Path("Q:/").exists():
        assert "Q" in found
    else:
        assert found == []
        assert any("Drive Q:" in w for w in warnings)


def test_vendor_umbrella_indexes_each_jetbrains_product(tmp_path: Path) -> None:
    jet = tmp_path / "Program Files" / "JetBrains"
    dg = jet / "DataGrip 2026"
    idea = jet / "IntelliJ IDEA"
    dg.mkdir(parents=True)
    idea.mkdir(parents=True)
    (dg / "datagrip64.exe").write_bytes(b"MZ")
    (idea / "idea64.exe").write_bytes(b"MZ")

    def fake_roots(drives: tuple[str, ...] = ("Z",), **_kwargs) -> list[Path]:
        return [tmp_path / "Program Files"]

    with (
        patch(
            "desktop_ai_agent.core.folder_index.indexer.existing_drives",
            return_value=["Z"],
        ),
        patch(
            "desktop_ai_agent.core.folder_index.indexer.discover_program_roots",
            side_effect=fake_roots,
        ),
    ):
        result = index_executables(
            drives=("Z",),
            include_start_menu=False,
            include_installed_apps=False,
            include_appx=False,
            include_games_folders=False,
        )

    names = {e["name"] for e in result.entries}
    assert any("datagrip" in n for n in names)
    assert any("idea" in n or "intellij" in n for n in names)


def test_lnk_parser_missing_file(tmp_path: Path) -> None:
    from desktop_ai_agent.core.folder_index.shortcuts import resolve_lnk_target

    assert resolve_lnk_target(tmp_path / "missing.lnk") is None
