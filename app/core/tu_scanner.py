"""Scanner for locally-stored Xbox 360 Title Updates.

Reads STFS package headers to extract TitleID, display name and version.

Expected layout (any nesting under LocalTitleUpdates/ is accepted):
    LocalTitleUpdates/
        {TitleID} - {GameName}/
            {tu_filename}          ← STFS package, no extension required
        or flat:
        {tu_filename}

STFS header offsets (big-endian):
    0x000  Magic (4 bytes): CON, LIVE, or PIRS
    0x344  Content type (4 bytes): 0x000B0000 for title updates
    0x360  Title ID (4 bytes)
    0x3A0  Title update version (4 bytes)
    0x411  Display name (UTF-16-BE, 128 bytes)
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

from app.models.title_update import TitleUpdateItem

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.parent
_LOCAL_TU_DIR = _REPO_ROOT / "LocalTitleUpdates"

_STFS_MAGIC = {b"CON ", b"LIVE", b"PIRS"}
_TU_CONTENT_TYPE = 0x000B0000
_HEADER_MIN = 0x500  # minimum bytes needed to read all fields


def _parse_stfs_header(path: Path) -> tuple[str | None, str, int]:
    """Read an STFS package and return (title_id_hex, display_name, version).

    Returns (None, filename, 0) if the file is not a recognisable STFS package.
    """
    try:
        with path.open("rb") as f:
            header = f.read(_HEADER_MIN)
    except OSError as e:
        log.debug("Cannot read %s: %s", path, e)
        return None, path.name, 0

    if len(header) < _HEADER_MIN:
        return None, path.name, 0

    magic = header[:4]
    if magic not in _STFS_MAGIC:
        return None, path.name, 0

    content_type = struct.unpack_from(">I", header, 0x344)[0]
    if content_type != _TU_CONTENT_TYPE:
        log.debug("%s has content type 0x%08X (not a TU) — skipping", path.name, content_type)
        return None, path.name, 0

    title_id_int = struct.unpack_from(">I", header, 0x360)[0]
    title_id = f"{title_id_int:08X}"

    version = struct.unpack_from(">I", header, 0x3A0)[0]

    name_raw = header[0x411:0x411 + 128]
    try:
        display_name = name_raw.decode("utf-16-be").rstrip("\x00").strip()
    except Exception:
        display_name = ""

    if not display_name:
        display_name = path.stem

    return title_id, display_name, version


# ---------------------------------------------------------------------------
# General-purpose STFS header reader (no content-type filter)
# ---------------------------------------------------------------------------

class StfsInfo:
    """Parsed subset of an STFS package header."""
    __slots__ = ("magic", "title_id", "media_id", "title_name", "error")

    def __init__(
        self,
        magic: str = "",
        title_id: str = "",
        media_id: str = "",
        title_name: str = "",
        error: str = "",
    ) -> None:
        self.magic = magic
        self.title_id = title_id
        self.media_id = media_id
        self.title_name = title_name
        self.error = error

    @property
    def ok(self) -> bool:
        return not self.error


def read_stfs_info(path: str | Path) -> "StfsInfo":
    """Read STFS header fields from *path* without filtering on content type.

    Works on any STFS package — game files, title updates, DLC, etc.
    Returns an :class:`StfsInfo` with ``error`` set if the file cannot be read
    or is not a valid STFS package.

    STFS header offsets (big-endian):
        0x000  Magic (4 bytes): CON, LIVE or PIRS
        0x354  Media ID (4 bytes)
        0x360  Title ID (4 bytes)
        0x411  Display name (UTF-16-BE, up to 256 bytes / 128 chars)
    """
    p = Path(path)
    try:
        with p.open("rb") as f:
            data = f.read(0x520)
    except FileNotFoundError:
        return StfsInfo(error=f"File not found: {p}")
    except OSError as e:
        return StfsInfo(error=f"Cannot read file: {e}")

    if len(data) < 0x520:
        return StfsInfo(error="File too small to be a valid STFS package")

    magic = data[0:4].decode("ascii", errors="replace").rstrip("\x00")
    if magic not in ("CON ", "LIVE", "PIRS"):
        return StfsInfo(error=f"Not an STFS package (magic: {magic!r})")

    title_id = data[0x360:0x364].hex().upper()
    media_id = data[0x354:0x358].hex().upper()
    try:
        title_name = data[0x411:0x511].decode("utf-16-be", errors="replace").rstrip("\x00").strip()
    except Exception:
        title_name = ""

    return StfsInfo(
        magic=magic,
        title_id=title_id,
        media_id=media_id,
        title_name=title_name or p.stem,
    )


# ---------------------------------------------------------------------------
# Unified game-file reader (STFS + ISO)
# ---------------------------------------------------------------------------

import re
import subprocess

_STFS_MAGICS_STR = {"CON ", "LIVE", "PIRS"}


def read_game_info(path: str | Path) -> StfsInfo:
    """Read Title ID and Media ID from either an STFS container or an Xbox 360 ISO.

    - STFS containers (GOD/CON/LIVE/PIRS): parsed directly from the binary header,
      no external tools required.
    - ISO files: parsed by calling ``abgx360`` as a subprocess and parsing its
      stdout/stderr.  Requires ``abgx360`` to be installed on the system.

    Title Update files are always STFS, so they should still go through
    :func:`read_stfs_info` directly.  Use this function only for game files,
    which may be in either format.

    Returns a :class:`StfsInfo` instance.  On failure ``StfsInfo.ok`` is
    ``False`` and ``StfsInfo.error`` contains a human-readable reason.
    """
    p = Path(path)
    try:
        with p.open("rb") as f:
            magic_bytes = f.read(4).decode("ascii", errors="replace")
    except FileNotFoundError:
        return StfsInfo(error=f"File not found: {p}")
    except OSError as e:
        return StfsInfo(error=f"Cannot read file: {e}")

    if magic_bytes in _STFS_MAGICS_STR:
        return read_stfs_info(p)

    return _read_iso_info(p)


def _read_iso_info(path: Path) -> StfsInfo:
    """Extract Title ID and Media ID from an Xbox 360 ISO using ``abgx360``.

    ``abgx360`` is an external command-line tool; if it is not installed an
    :class:`StfsInfo` with a clear error message is returned rather than raising.
    """
    try:
        result = subprocess.run(
            ["abgx360", "--noupdate", "-w", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return StfsInfo(
            error=(
                "abgx360 is not installed — it is required to read ISO files. "
                "See the README (External Dependencies) for install instructions."
            )
        )
    except subprocess.TimeoutExpired:
        return StfsInfo(error="abgx360 timed out reading the ISO.")
    except OSError as e:
        return StfsInfo(error=f"Failed to run abgx360: {e}")

    output = result.stdout + result.stderr

    title_id_match = re.search(r"Title ID\s*[:\-]\s*([0-9A-Fa-f]{8})", output)
    media_id_match = re.search(r"Media ID\s*[:\-]\s*([0-9A-Fa-f]{8})", output)
    title_name_match = re.search(r"Title Name\s*[:\-]\s*(.+)", output)

    if not title_id_match or not media_id_match:
        return StfsInfo(
            error=f"abgx360 could not parse Title ID / Media ID from: {path.name}"
        )

    return StfsInfo(
        magic="ISO",
        title_id=title_id_match.group(1).upper(),
        media_id=media_id_match.group(1).upper(),
        title_name=title_name_match.group(1).strip() if title_name_match else path.stem,
    )


# ---------------------------------------------------------------------------
# Local TU scanner
# ---------------------------------------------------------------------------

def scan_local_title_updates(path: str | Path | None = None) -> list[TitleUpdateItem]:
    """Scan *path* (defaults to LocalTitleUpdates/) for TU STFS packages.

    Each file is inspected for a valid STFS header with content type 0x000B0000.
    Returns a list of :class:`TitleUpdateItem` sorted by TitleID then display name.
    """
    root = Path(path) if path else _LOCAL_TU_DIR
    if not root.is_dir():
        log.info("LocalTitleUpdates directory not found: %s", root)
        return []

    items: list[TitleUpdateItem] = []

    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        if f.name.startswith("."):
            continue

        title_id, display_name, version = _parse_stfs_header(f)
        if title_id is None:
            log.debug("Skipping non-TU file: %s", f.name)
            continue

        items.append(TitleUpdateItem(
            title_id=title_id,
            display_name=display_name,
            version=version,
            local_path=f,
        ))
        log.debug("Found TU: %s v%s (%s)", display_name, version, title_id)

    log.info("TU scan found %d update(s) in %s", len(items), root)
    items.sort(key=lambda i: (i.title_id, i.display_name))
    return items
