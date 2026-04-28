"""Game cheat dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheatOffset:
    value_type: str = ""
    data_type: str = ""
    opcode: str = ""
    offset: str = ""
    value: str = ""

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "CheatOffset":
        return cls(
            value_type=d.get("ValueType", "") or "",
            data_type=d.get("DataType", "") or "",
            opcode=d.get("Opcode", "") or "",
            offset=d.get("Offset", "") or "",
            value=d.get("Value", "") or "",
        )


@dataclass
class CheatEntry:
    name: str = ""
    offsets: list[CheatOffset] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "CheatEntry":
        return cls(
            name=d.get("Name", "") or "",
            offsets=[CheatOffset.from_json(x) for x in (d.get("Offsets") or [])],
        )


@dataclass
class GameCheatsData:
    game: str = ""
    region: str = ""
    version: str = "-"
    cheats: list[CheatEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "GameCheatsData":
        return cls(
            game=d.get("Game", "") or "",
            region=d.get("Region", "") or "",
            version=d.get("Version", "-") or "-",
            cheats=[CheatEntry.from_json(x) for x in (d.get("Cheats") or [])],
        )
