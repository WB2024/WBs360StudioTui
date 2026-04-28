"""Game patch dataclasses (parsed from rpcs3-style yml/json patches)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PatchEntry:
    name: str = ""
    author: str = ""
    description: str = ""
    is_enabled: bool = False

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "PatchEntry":
        return cls(
            name=d.get("name", "") or "",
            author=d.get("author", "") or "",
            description=d.get("desc", "") or "",
            is_enabled=bool(d.get("is_enabled", False)),
        )


@dataclass
class GamePatchItemData:
    hash: str = ""
    title_id: str = ""
    title_name: str = ""
    patches: list[PatchEntry] = field(default_factory=list)
    source_file: str = ""

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "GamePatchItemData":
        return cls(
            hash=d.get("hash", "") or "",
            title_id=d.get("title_id", "") or "",
            title_name=d.get("title_name", "") or "",
            patches=[PatchEntry.from_json(x) for x in (d.get("patch") or [])],
        )
