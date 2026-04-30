"""Title Update item model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TitleUpdateItem:
    """A locally-stored Xbox 360 Title Update (STFS package, content type 000B0000)."""

    title_id: str       # 8-char uppercase hex, e.g. "584111F7"
    display_name: str   # From STFS header display name field, or filename fallback
    version: int        # TU version extracted from STFS header (0 if unreadable)
    local_path: Path    # Absolute path to the TU file

    @property
    def filename(self) -> str:
        return self.local_path.name

    @property
    def size_bytes(self) -> int:
        try:
            return self.local_path.stat().st_size
        except OSError:
            return 0

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        else:
            return f"{b / 1024 ** 3:.2f} GB"

    @property
    def version_str(self) -> str:
        return f"TU{self.version}" if self.version > 0 else "Unknown"
