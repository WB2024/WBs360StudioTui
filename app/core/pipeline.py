"""Game processing pipeline — scan, convert, tidy, transfer.

Workflow:
  0. (Optional) Detect archive files (.zip/.7z/.rar/…) and extract selected
     ones with 7zip before the game scan.
  1. Scan torrent download folder for ISO files and GOD containers.
  2. For each ISO: convert to GOD via iso2god.
  3. For each GOD (newly converted or already present): apply local folder-name
     format (e.g. "Name/TitleID") before transfer.
  4. Transfer to console via FTP or USB.

Multi-disc handling: each .iso file is converted independently to its own
GOD container. The naming tidy step uses the game name derived from the
folder that contained the ISO.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from app.core.iso2god import ConversionProgress, Iso2GodError, convert_iso
from app.models.god_game import GodGameItem

log = logging.getLogger(__name__)

_ISO_EXT = {".iso"}
ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".lzma", ".zst"}


# ── Status enum ───────────────────────────────────────────────────────────────

class GameStatus(Enum):
    PENDING = auto()
    CONVERTING = auto()
    CONVERTED = auto()
    TIDYING = auto()
    TRANSFERRING = auto()
    DONE = auto()
    SKIPPED = auto()
    ERROR = auto()


# ── Discovery models ──────────────────────────────────────────────────────────

@dataclass
class DiscoveredArchive:
    """An archive file found inside the download folder."""
    name: str           # Filename without extension
    archive_path: Path  # Absolute path to the archive
    ext: str            # e.g. ".zip", ".7z"
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 ** 2)


@dataclass
class DiscoveredIso:
    """An ISO file found inside the download folder."""
    name: str           # Game name (from parent folder or file stem)
    iso_path: Path      # Absolute path to the .iso file
    disc_label: str     # e.g. "disc1" / "" (empty for single-disc)


@dataclass
class DiscoveredGod:
    """A GOD container found directly in the download folder."""
    name: str
    god: GodGameItem


@dataclass
class PipelineGame:
    """One game travelling through the pipeline."""
    name: str
    # Exactly one of these is populated at discovery time:
    iso: Optional[DiscoveredIso] = None
    god: Optional[GodGameItem] = None

    # Filled in as pipeline progresses
    status: GameStatus = GameStatus.PENDING
    status_detail: str = ""
    converted_god: Optional[GodGameItem] = None  # set after ISO→GOD step
    final_god: Optional[GodGameItem] = None       # set after local tidy step

    @property
    def is_god_source(self) -> bool:
        return self.god is not None

    @property
    def display_type(self) -> str:
        if self.iso:
            return f"ISO ({self.iso.disc_label})" if self.iso.disc_label else "ISO"
        return "GOD"


# ── Scan ──────────────────────────────────────────────────────────────────────

def scan_download_folder(folder: str | Path) -> list[PipelineGame]:
    """Scan *folder* for ISOs and GOD containers.

    Returns one PipelineGame per ISO file (multi-disc = multiple entries)
    and one per GOD container found.
    """
    root = Path(folder)
    if not root.is_dir():
        log.warning("Download folder not found: %s", root)
        return []

    games: list[PipelineGame] = []

    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _ISO_EXT:
            # Flat ISO directly in the folder
            games.append(PipelineGame(
                name=entry.stem,
                iso=DiscoveredIso(name=entry.stem, iso_path=entry.resolve(), disc_label=""),
            ))

        elif entry.is_dir():
            # Check if it's already a GOD container (has 8-hex TitleID sub-folder)
            god_items = _try_scan_god_dir(entry)
            if god_items:
                for g in god_items:
                    games.append(PipelineGame(name=entry.name, god=g))
            else:
                # Look for ISOs inside the folder
                isos = _find_isos_in_dir(entry)
                for iso_path, disc_label in isos:
                    name = entry.name
                    games.append(PipelineGame(
                        name=name,
                        iso=DiscoveredIso(name=name, iso_path=iso_path, disc_label=disc_label),
                    ))

    log.info("Pipeline scan found %d item(s) in %s", len(games), root)
    return games


def _find_isos_in_dir(folder: Path) -> list[tuple[Path, str]]:
    """Return (iso_path, disc_label) for every .iso inside *folder* (1 level deep)."""
    results: list[tuple[Path, str]] = []
    iso_files = sorted(f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() in _ISO_EXT)

    if len(iso_files) == 1:
        results.append((iso_files[0].resolve(), ""))
    else:
        for f in iso_files:
            # Try to pick a disc label from the filename (disc1, disc2, etc.)
            m = re.search(r"(disc\s*\d+)", f.stem, re.IGNORECASE)
            label = m.group(1).lower().replace(" ", "") if m else f.stem
            results.append((f.resolve(), label))

    return results


def _try_scan_god_dir(folder: Path) -> list[GodGameItem]:
    """Return GOD items if *folder* looks like a GOD container, else []."""
    from app.core.god_scanner import _scan_for_title_ids
    games: list[GodGameItem] = []
    _scan_for_title_ids(folder, folder.name, games)
    return games


# ── Local rename (tidy) ───────────────────────────────────────────────────────

def local_god_rename(
    god: GodGameItem,
    friendly_name: str,
    target_format: str,
    god_output_root: Path,
) -> GodGameItem:
    """Move/rename a GOD game's parent folder to match *target_format*.

    The GOD item's ``local_path`` points to the content-type folder, e.g.:
       /path/to/games/SomeGame/TitleID/00007000/

    We rename the top-level parent of ``TitleID/`` (i.e. "SomeGame") to match
    the desired naming convention, then return an updated GodGameItem.

    If the folder is already correctly named, no move is performed.

    Supported formats (from game_tidy.py constants):
      "TitleID"                → {TitleID}/
      "Name/TitleID"           → {Name}/{TitleID}/
      "Name - TitleID"         → {Name} - {TitleID}/{TitleID}/
      "TitleID - Name"         → {TitleID} - {Name}/{TitleID}/
    """
    from app.core.game_tidy import (
        FORMAT_NAME_DASH_TITLE_ID,
        FORMAT_NAME_SLASH_TITLE_ID,
        FORMAT_TITLE_ID,
        FORMAT_TITLE_ID_DASH_NAME,
    )

    tid = god.title_id
    name = friendly_name or god.name

    if target_format == FORMAT_TITLE_ID:
        new_parent_name = tid
    elif target_format == FORMAT_NAME_SLASH_TITLE_ID:
        new_parent_name = name
    elif target_format == FORMAT_NAME_DASH_TITLE_ID:
        new_parent_name = f"{name} - {tid}"
    elif target_format == FORMAT_TITLE_ID_DASH_NAME:
        new_parent_name = f"{tid} - {name}"
    else:
        new_parent_name = name

    # god.local_path = .../content_type/
    # parent of that is TitleID dir; parent of that is the "game root"
    ct_dir = god.local_path          # e.g. /games/SomeGame/45410822/00007000
    tid_dir = ct_dir.parent          # e.g. /games/SomeGame/45410822
    old_game_root = tid_dir.parent   # e.g. /games/SomeGame

    if target_format == FORMAT_TITLE_ID:
        # TitleID is the game root — TitleID/ directly inside god_output_root
        new_game_root = god_output_root / tid
    else:
        new_game_root = god_output_root / new_parent_name

    new_tid_dir = new_game_root / tid
    new_ct_dir = new_tid_dir / god.content_type

    if new_ct_dir == ct_dir:
        log.debug("GOD already in correct location: %s", ct_dir)
        return god

    log.info("Moving GOD: %s → %s", old_game_root, new_game_root)
    new_game_root.mkdir(parents=True, exist_ok=True)

    # Move the TitleID folder into the new game root
    shutil.move(str(tid_dir), str(new_tid_dir))

    # Remove old game root if now empty
    try:
        if old_game_root != god_output_root and not any(old_game_root.iterdir()):
            old_game_root.rmdir()
    except Exception:
        pass

    return GodGameItem(
        name=name,
        title_id=god.title_id,
        content_type=god.content_type,
        local_path=new_ct_dir,
        container_file=god.container_file,
    )


# ── Conversion step ───────────────────────────────────────────────────────────

async def convert_iso_to_god(
    iso: DiscoveredIso,
    god_output_root: Path,
    binary_path: Path,
    on_progress: Optional[Callable[[ConversionProgress], None]] = None,
) -> GodGameItem:
    """Convert an ISO to GOD format. Returns a GodGameItem for the result.

    Raises Iso2GodError on failure.
    """
    god_output_root.mkdir(parents=True, exist_ok=True)

    # Use game name as the output folder label
    out_dir = god_output_root / iso.name
    out_dir.mkdir(parents=True, exist_ok=True)

    final_progress = await convert_iso(
        iso_path=iso.iso_path,
        dest_dir=out_dir,
        binary=binary_path,
        num_threads=2,
        trim=True,
        game_title=iso.name,
        on_progress=on_progress,
    )

    # Scan the output folder for the resulting GOD structure
    from app.core.god_scanner import _scan_for_title_ids
    items: list[GodGameItem] = []
    _scan_for_title_ids(out_dir, iso.name, items)

    if not items:
        raise Iso2GodError(
            f"iso2god completed but no GOD structure found in {out_dir}"
        )

    return items[0]


# ── Archive scanning and extraction ───────────────────────────────────────────

_7ZIP_CANDIDATES = ["7z", "7za", "7zz", "7zzs"]


def find_7zip() -> str | None:
    """Return the path to a 7-zip executable, or None if not found."""
    for candidate in _7ZIP_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return None


def scan_archives(folder: str | Path) -> list[DiscoveredArchive]:
    """Scan *folder* (1 level deep) for archive files.

    Returns one DiscoveredArchive per archive found at the top level or
    inside a single-level subfolder. Archives nested deeper are ignored.
    """
    root = Path(folder)
    if not root.is_dir():
        return []

    archives: list[DiscoveredArchive] = []

    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() in ARCHIVE_EXTS:
            archives.append(DiscoveredArchive(
                name=entry.stem,
                archive_path=entry.resolve(),
                ext=entry.suffix.lower(),
                size_bytes=entry.stat().st_size,
            ))
        elif entry.is_dir():
            # Also check one level inside subfolders
            for sub in sorted(entry.iterdir()):
                if sub.is_file() and sub.suffix.lower() in ARCHIVE_EXTS:
                    archives.append(DiscoveredArchive(
                        name=sub.stem,
                        archive_path=sub.resolve(),
                        ext=sub.suffix.lower(),
                        size_bytes=sub.stat().st_size,
                    ))

    log.info("Archive scan found %d archive(s) in %s", len(archives), root)
    return archives


class ExtractionError(Exception):
    """Raised when 7zip extraction fails."""


async def extract_archive(
    archive: DiscoveredArchive,
    dest_dir: Path,
    seven_zip: str,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Extract *archive* into *dest_dir* using 7zip.

    *on_line* is called with each line of 7zip stdout (progress/filenames).
    Raises ExtractionError if 7zip exits non-zero.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [seven_zip, "x", str(archive.archive_path), f"-o{dest_dir}", "-y"]
    log.info("Extracting %s → %s", archive.archive_path, dest_dir)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line and on_line:
            on_line(line)

    rc = await proc.wait()
    if rc != 0:
        raise ExtractionError(
            f"7zip exited with code {rc} extracting {archive.archive_path.name}"
        )
    log.info("Extraction complete: %s", archive.archive_path.name)

