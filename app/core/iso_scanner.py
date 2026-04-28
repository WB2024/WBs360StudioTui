"""Scanner for locally-stored Xbox 360 ISO files.

Expected directory structure (two common layouts both supported):
  {local_iso_path}/{GameName}/{GameName}.iso    ← subfolder per game
  {local_iso_path}/{GameName}.iso               ← flat
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class IsoGameItem:
    """Represents a locally-stored Xbox 360 ISO file."""

    name: str       # Derived from file stem or parent folder
    iso_path: Path  # Absolute path to the .iso file

    @property
    def size_bytes(self) -> int:
        try:
            return self.iso_path.stat().st_size
        except OSError:
            return 0

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)


def scan_iso_path(path: str | Path) -> list[IsoGameItem]:
    """Scan *path* for Xbox 360 ISO files. Returns empty list if path invalid."""
    root = Path(path) if path else None
    if not root or not root.is_dir():
        return []

    games: list[IsoGameItem] = []

    # Walk up to 2 levels deep: flat ISOs + one-folder-per-game
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".iso":
            games.append(IsoGameItem(name=entry.stem, iso_path=entry.resolve()))
        elif entry.is_dir():
            # Look for .iso files inside this subfolder
            for iso_file in sorted(entry.glob("*.iso")):
                if iso_file.is_file():
                    # Prefer parent folder name as game name (usually cleaner)
                    name = entry.name
                    games.append(IsoGameItem(name=name, iso_path=iso_file.resolve()))

    log.info("ISO scan found %d file(s) in %s", len(games), root)
    return games
