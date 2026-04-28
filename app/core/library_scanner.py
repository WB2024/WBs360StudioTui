"""FTP library scanner — discovers installed Title ID folders on Xbox 360."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.ftp_client import _ftp_path

if TYPE_CHECKING:
    from app.core.ftp_client import FtpClient

log = logging.getLogger(__name__)

# Xbox Title IDs are exactly 8 hex digits (case-insensitive).
TITLE_ID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
LIBRARY_CACHE_FILE = "library.json"
LIST_TIMEOUT = 20  # seconds per directory listing


def load_csv_titles(csv_path: Path) -> dict[str, str]:
    """Load gamelist_xbox360.csv → {TITLE_ID_UPPER: game_name}.

    The CSV is tab-separated with columns: title_id, media_id, title_name.
    """
    result: dict[str, str] = {}
    if not csv_path.exists():
        return result
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                tid = (row.get("title_id") or "").strip().upper()
                name = (row.get("title_name") or "").strip()
                if tid and name:
                    result[tid] = name
    except Exception:
        log.exception("Failed loading CSV game list from %s", csv_path)
    return result


async def scan_library(
    client: "FtpClient",
    game_paths: list[str],
    scan_depth: int,
    progress_callback=None,
) -> dict[str, str]:
    """Scan FTP game paths for Xbox Title ID folders.

    Traverses up to `scan_depth` directory levels under each game path,
    treating any folder whose name is exactly 8 hex digits as a Title ID.

    Args:
        client: Connected FtpClient.
        game_paths: List of Xbox-style paths (e.g. ["Usb1\\Games"]).
        scan_depth: Max folder depth to recurse.
        progress_callback: Optional callable(message: str) for status updates.

    Returns:
        dict mapping TITLE_ID_UPPER → ftp_path of the Title ID folder.
    """
    found: dict[str, str] = {}
    for raw_path in game_paths:
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        ftp_root = _ftp_path(raw_path)
        log.debug("Scanning %s (depth=%d)", ftp_root, scan_depth)
        if progress_callback:
            progress_callback(f"Scanning {ftp_root}…")
        await _scan_recursive(client, ftp_root, scan_depth, found)
    return found


async def _scan_recursive(
    client: "FtpClient",
    ftp_path: str,
    depth: int,
    found: dict[str, str],
) -> None:
    """Recursively list ftp_path, collecting Title ID folders."""
    if depth <= 0:
        return
    inner = client._client  # type: ignore[attr-defined]
    if inner is None:
        return

    entries: list[tuple[str, bool]] = []
    try:
        # raw_command=True forces LIST instead of MLSD (Aurora doesn't support MLSD).
        async with asyncio.timeout(LIST_TIMEOUT):
            async for path_obj, info in inner.list(ftp_path, raw_command=True):
                name = path_obj.name
                is_dir = info.get("type", "").lower() in ("dir", "cdir", "pdir", "")
                entries.append((name, is_dir))
    except Exception as exc:
        log.debug("list(%s) failed: %s", ftp_path, exc)
        return

    for name, is_dir in entries:
        if not name or name in (".", ".."):
            continue
        if TITLE_ID_RE.match(name):
            tid = name.upper()
            found[tid] = f"{ftp_path}/{name}"
            log.info("Found Title ID: %s at %s/%s", tid, ftp_path, name)
        elif is_dir and depth > 1:
            await _scan_recursive(client, f"{ftp_path}/{name}", depth - 1, found)


def save_library(library: dict[str, str], cache_dir: Path) -> None:
    """Persist library dict to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / LIBRARY_CACHE_FILE
    path.write_text(json.dumps(library, indent=2), encoding="utf-8")


def load_library(cache_dir: Path) -> dict[str, str]:
    """Load cached library dict, returning empty dict if not found."""
    path = cache_dir / LIBRARY_CACHE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k).upper(): str(v) for k, v in data.items()}
    except Exception:
        log.exception("Failed loading library cache")
    return {}
