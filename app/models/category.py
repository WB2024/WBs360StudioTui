"""Category dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CategoryItem:
    id: str = ""
    title: str = ""
    type: str = "game"
    regions: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "CategoryItem":
        return cls(
            id=d.get("Id", "") or "",
            title=d.get("Title", "") or "",
            type=d.get("Type", "game") or "game",
            regions=list(d.get("Regions") or []),
        )
