"""Dataclasses for Arisen Studio JSON entities."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DownloadFile:
    name: str = ""
    region: str = ""
    version: str = ""
    last_updated: str = ""
    url: str = ""
    install_paths: list[str] = field(default_factory=list)
    local_path: str = ""   # set when source=="local"; skips HTTP download

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "DownloadFile":
        return cls(
            name=d.get("Name", "") or "",
            region=d.get("Region", "") or "",
            version=d.get("Version", "") or "",
            last_updated=d.get("LastUpdated", "") or "",
            url=d.get("Url", "") or "",
            install_paths=list(d.get("InstallPaths") or []),
        )


@dataclass
class ModItemData:
    id: int = 0
    platform: str = ""
    category_id: str = ""
    name: str = ""
    firmware_type: str = "n/a"
    region: str = "n/a"
    created_by: str = "Unknown"
    submitted_by: str = ""
    version: str = ""
    game_mode: str = ""
    mod_type: str = ""
    description: str = ""
    download_files: list[DownloadFile] = field(default_factory=list)
    source: str = "online"  # "online" | "local"

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ModItemData":
        return cls(
            id=int(d.get("Id", 0) or 0),
            platform=d.get("Platform", "") or "",
            category_id=d.get("CategoryId", "") or "",
            name=d.get("Name", "") or "",
            firmware_type=d.get("FirmwareType", "n/a") or "n/a",
            region=d.get("Region", "n/a") or "n/a",
            created_by=d.get("CreatedBy", "Unknown") or "Unknown",
            submitted_by=d.get("SubmittedBy", "") or "",
            version=d.get("Version", "") or "",
            game_mode=d.get("GameMode", "") or "",
            mod_type=d.get("ModType", "") or "",
            description=d.get("Description", "") or "",
            download_files=[DownloadFile.from_json(x) for x in (d.get("DownloadFiles") or [])],
        )
