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
    """Recursively scan *folder* for ISOs and GOD containers at any depth.

    Returns one PipelineGame per ISO file (multi-disc = multiple entries)
    and one per GOD container found.

    Game name is taken from the immediate parent folder, or the file stem
    for flat ISOs sitting directly in *folder*.
    """
    root = Path(folder)
    if not root.is_dir():
        log.warning("Download folder not found: %s", root)
        return []

    games: list[PipelineGame] = []
    # Track which dirs we already handled as GOD containers to avoid
    # also picking up their individual files as ISOs.
    god_roots: set[Path] = set()

    # ── Pass 1: find GOD containers (directories with a TitleID sub-folder) ──
    # Walk all directories looking for the TitleID pattern.
    # We also include root itself so that TitleID folders extracted directly
    # into root (with no parent game folder) are detected.
    all_dirs = [root] + sorted(e for e in root.rglob("*") if e.is_dir())
    seen_god_items: set[Path] = set()  # deduplicate across root + rglob passes
    for entry in all_dirs:
        god_items = _try_scan_god_dir(entry)
        if god_items:
            new_items = [g for g in god_items if g.local_path not in seen_god_items]
            if not new_items:
                continue
            for g in new_items:
                seen_god_items.add(g.local_path)
                # For root-level scans use TitleID as name (no parent folder available)
                name = entry.name if entry != root else g.title_id
                games.append(PipelineGame(name=name, god=g))
            # Only add entry to god_roots if it's not root itself; adding root
            # would cause all ISOs in the tree to be skipped in Pass 2.
            if entry != root:
                god_roots.add(entry.resolve())
            else:
                # Add the specific TitleID dirs found (not root itself)
                for g in new_items:
                    god_roots.add(g.local_path.parent.resolve())

    # ── Pass 2: find ISO files not inside a known GOD container ──
    # Group by parent dir so multi-disc detection works.
    iso_by_parent: dict[Path, list[Path]] = {}
    for iso_file in sorted(root.rglob("*")):
        if not iso_file.is_file() or iso_file.suffix.lower() not in _ISO_EXT:
            continue
        # Skip ISOs that live inside a recognised GOD container dir
        if any(iso_file.resolve().is_relative_to(gr) for gr in god_roots):
            continue
        parent = iso_file.parent.resolve()
        iso_by_parent.setdefault(parent, []).append(iso_file.resolve())

    for parent, iso_files in sorted(iso_by_parent.items()):
        # Derive game name: parent folder (unless that's the root itself)
        folder_name = parent.name if parent != root.resolve() else None
        isos = sorted(iso_files)
        if len(isos) == 1:
            name = folder_name or isos[0].stem
            games.append(PipelineGame(
                name=name,
                iso=DiscoveredIso(name=name, iso_path=isos[0], disc_label=""),
            ))
        else:
            for iso_path in isos:
                m = re.search(r"(disc\s*\d+)", iso_path.stem, re.IGNORECASE)
                label = m.group(1).lower().replace(" ", "") if m else iso_path.stem
                name = folder_name or iso_path.stem
                games.append(PipelineGame(
                    name=name,
                    iso=DiscoveredIso(name=name, iso_path=iso_path, disc_label=label),
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
    """Recursively scan *folder* for archive files at any depth.

    Returns one DiscoveredArchive per archive found.
    """
    root = Path(folder)
    if not root.is_dir():
        return []

    archives: list[DiscoveredArchive] = []
    for entry in sorted(root.rglob("*")):
        if entry.is_file() and entry.suffix.lower() in ARCHIVE_EXTS:
            archives.append(DiscoveredArchive(
                name=entry.stem,
                archive_path=entry.resolve(),
                ext=entry.suffix.lower(),
                size_bytes=entry.stat().st_size,
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

