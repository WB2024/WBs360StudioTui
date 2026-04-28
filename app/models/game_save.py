"""Game save dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mod_item import DownloadFile


@dataclass
class GameSaveItemData:
    id: int = 0
    platform: str = ""
    category_id: str = ""
    name: str = ""
    region: str = ""
    last_updated: str = ""
    created_by: str = ""
    submitted_by: str = ""
    version: str = ""
    game_mode: str = ""
    description: str = ""
    download_files: list[DownloadFile] = field(default_factory=list)
    source: str = "online"  # "online" | "local"

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "GameSaveItemData":
        return cls(
            id=int(d.get("Id", 0) or 0),
            platform=d.get("Platform", "") or "",
            category_id=d.get("CategoryId", "") or "",
            name=d.get("Name", "") or "",
            region=d.get("Region", "") or "",
            last_updated=str(d.get("LastUpdated", "") or ""),
            created_by=d.get("CreatedBy", "") or "",
            submitted_by=d.get("SubmittedBy", "") or "",
            version=d.get("Version", "") or "",
            game_mode=d.get("GameMode", "") or "",
            description=d.get("Description", "") or "",
            download_files=[DownloadFile.from_json(x) for x in (d.get("DownloadFiles") or [])],
        )
