"""Trainer dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrainerItem:
    name: str = ""
    type: str = ""  # "aurora" or "xbdm"
    url: str = ""
    last_updated: str = ""
    install_paths: list[str] = field(default_factory=list)
    source: str = "online"  # "online" | "local"
    local_path: str = ""   # absolute path when source=="local"

    @property
    def trainer_type(self) -> str:
        return "Aurora" if (self.type or "").lower() == "aurora" else "XBDM"

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "TrainerItem":
        return cls(
            name=d.get("Name", "") or "",
            type=d.get("Type", "") or "",
            url=d.get("Url", "") or "",
            last_updated=str(d.get("LastUpdated", "") or ""),
            install_paths=list(d.get("InstallPaths") or []),
        )


@dataclass
class TrainerGameItem:
    title_id: str = ""
    description: str = ""
    trainers: list[TrainerItem] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "TrainerGameItem":
        return cls(
            title_id=d.get("TitleId", "") or "",
            description=d.get("Description", "") or "",
            trainers=[TrainerItem.from_json(x) for x in (d.get("Trainers") or [])],
        )
