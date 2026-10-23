"""Discover Windows Store / AppX apps (AUMID) via a single Shell:AppsFolder query."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


def collect_appx_launches(
    *,
    max_apps: int = 5_000,
    warnings: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Return AppX / Shell:AppsFolder entries with an AUMID launch id.

    Each entry shape matches the exe catalog plus ``aumid`` and empty ``path``
    (launch goes through ``shell:AppsFolder\\<aumid>``).
    """
    warn = warnings if warnings is not None else []
    if sys.platform != "win32":
        return []

    # One PowerShell process for the whole AppsFolder — not per app.
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject Shell.Application
$folder = $shell.NameSpace('shell:AppsFolder')
if (-not $folder) { '[]'; exit 0 }
$rows = @()
foreach ($item in $folder.Items()) {
  $p = [string]$item.Path
  $n = [string]$item.Name
  if (-not $p -or -not $n) { continue }
  # Classic .lnk / exe paths are already covered elsewhere; keep AUMID-like ids.
  if ($p -match '^[A-Za-z]:\\' -or $p.EndsWith('.lnk') -or $p.EndsWith('.exe')) { continue }
  if ($p -notmatch '!') { continue }
  $rows += [pscustomobject]@{ name = $n; aumid = $p }
}
$rows | ConvertTo-Json -Compress -Depth 3
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"AppX AppsFolder query failed: {exc}"
        warn.append(msg)
        logger.warning(msg)
        return []

    raw = (result.stdout or "").strip()
    if not raw:
        if result.returncode != 0:
            msg = f"AppX AppsFolder query empty (code={result.returncode})"
            warn.append(msg)
            logger.warning(msg)
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"AppX AppsFolder JSON parse failed: {exc}"
        warn.append(msg)
        logger.warning(msg)
        return []

    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return []

    from desktop_ai_agent.core.folder_index.indexer import (
        _looks_like_uninstall,
        normalize_name,
    )

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if len(found) >= max_apps:
            break
        if not isinstance(row, dict):
            continue
        display = str(row.get("name") or "").strip()
        aumid = str(row.get("aumid") or "").strip()
        if not display or not aumid or "!" not in aumid:
            continue
        if _looks_like_uninstall(display):
            continue
        key = aumid.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "name": normalize_name(display),
                "display": display,
                "path": "",
                "aumid": aumid,
                "folder": "",
                "root": "",
                "link": "",
                "source": "appx",
                "aliases": [],
            }
        )
    return found


def launch_aumid(aumid: str) -> None:
    """Activate a Store/AppX app by AUMID via shell:AppsFolder."""
    import os

    if not aumid or "!" not in aumid:
        raise ValueError(f"Invalid AUMID: {aumid!r}")
    # explorer / startfile both accept shell:AppsFolder\<AUMID>
    target = f"shell:AppsFolder\\{aumid}"
    if hasattr(os, "startfile"):
        os.startfile(target)  # noqa: S606 — intentional Windows shell activate
        return
    raise RuntimeError("AUMID launch requires Windows os.startfile")
