"""GOD (Games on Demand) game item model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class GodGameItem:
    """Represents a locally-stored GOD format game ready for transfer."""

    name: str           # Human-readable name derived from folder name, e.g. "Dead Rising"
    title_id: str       # 8-char hex TitleID, e.g. "434307D2"
    content_type: str   # Content type folder, e.g. "00007000"
    local_path: Path    # Absolute path to the {content_type}/ folder on disk
    container_file: str # Name of the CON header file, e.g. "958B279031EA57DF3AEB"

    def all_files(self) -> list[tuple[Path, str]]:
        """Return (absolute_local_file, relative_path_from_content_type_folder) pairs."""
        out: list[tuple[Path, str]] = []
        for f in sorted(self.local_path.rglob("*")):
            if f.is_file():
                rel = str(f.relative_to(self.local_path)).replace("\\", "/")
                out.append((f, rel))
        return out

    @property
    def file_count(self) -> int:
        return sum(1 for f in self.local_path.rglob("*") if f.is_file())

    @property
    def total_size_bytes(self) -> int:
        return sum(f.stat().st_size for f, _ in self.all_files())

    @property
    def total_size_gb(self) -> float:
        return self.total_size_bytes / (1024 ** 3)
