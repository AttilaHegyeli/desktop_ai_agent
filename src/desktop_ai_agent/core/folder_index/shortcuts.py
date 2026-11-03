"""Resolve Windows .lnk targets by parsing the Shell Link binary (MS-SHLLINK).

No per-file PowerShell and no COM dependency — local file targets only.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable


def resolve_lnk_target(lnk: Path | str) -> str | None:
    """
    Return the local filesystem target of a ``.lnk``, or None.

    Handles common desktop/Start Menu shortcuts that point at a local path.
    Network / shell-item-only links without LinkInfo may return None.
    """
    path = Path(lnk)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return _parse_lnk_local_path(data)


def resolve_lnk_targets(links: Iterable[Path | str]) -> dict[str, str]:
    """Resolve many shortcuts; returns ``{lnk_path: target_path}``."""
    out: dict[str, str] = {}
    for lnk in links:
        key = str(Path(lnk))
        target = resolve_lnk_target(key)
        if target:
            out[key] = target
    return out


def _parse_lnk_local_path(data: bytes) -> str | None:
    # Shell Link header: size must be 0x4C, CLSID follows.
    if len(data) < 0x4C:
        return None
    header_size = struct.unpack_from("<I", data, 0)[0]
    if header_size != 0x4C:
        return None

    link_flags = struct.unpack_from("<I", data, 0x14)[0]
    has_id_list = bool(link_flags & 0x01)
    has_link_info = bool(link_flags & 0x02)
    is_unicode = bool(link_flags & 0x80)

    offset = header_size
    if has_id_list:
        if offset + 2 > len(data):
            return None
        id_list_size = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + id_list_size

    if not has_link_info:
        return None
    if offset + 4 > len(data):
        return None

    link_info_size = struct.unpack_from("<I", data, offset)[0]
    if link_info_size < 0x1C or offset + link_info_size > len(data):
        return None

    link_info = data[offset : offset + link_info_size]
    link_info_header = struct.unpack_from("<I", link_info, 0)[0]
    if link_info_header < 0x1C:
        return None
    link_info_flags = struct.unpack_from("<I", link_info, 0x8)[0]
    local_base_path_offset = struct.unpack_from("<I", link_info, 0x10)[0]

    # VolumeIDAndLocalBasePath
    if not (link_info_flags & 0x01):
        return None

    path: str | None = None
    # Prefer Unicode local base path when present (header >= 0x24).
    if link_info_header >= 0x24:
        unicode_offset = struct.unpack_from("<I", link_info, 0x1C)[0]
        if unicode_offset and unicode_offset < link_info_size:
            path = _read_c_wstring(link_info, unicode_offset)

    if not path and local_base_path_offset and local_base_path_offset < link_info_size:
        path = _read_c_string(link_info, local_base_path_offset)

    if not path:
        return None

    # Optional CommonPathSuffix append is rare for absolute local targets.
    return path.strip() or None


def _read_c_string(buf: bytes, offset: int) -> str | None:
    if offset < 0 or offset >= len(buf):
        return None
    end = buf.find(b"\x00", offset)
    if end < 0:
        end = len(buf)
    try:
        return buf[offset:end].decode("cp1252", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _read_c_wstring(buf: bytes, offset: int) -> str | None:
    if offset < 0 or offset + 1 >= len(buf):
        return None
    chars: list[str] = []
    i = offset
    while i + 1 < len(buf):
        code = struct.unpack_from("<H", buf, i)[0]
        i += 2
        if code == 0:
            break
        chars.append(chr(code))
    return "".join(chars) or None
