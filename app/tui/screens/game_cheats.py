from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class GameCheatsScreen(BrowserScreen):
    TITLE = "Game Cheats"
    SEARCH_PLACEHOLDER = "Search by game / cheat..."

    # Override: cheats can't be installed; use 'd' to copy info
    BINDINGS = [b for b in BrowserScreen.BINDINGS if getattr(b, "key", "") not in ("i", "d")]

    def get_columns(self) -> list[str]:
        return ["Game", "Cheat", "Region", "Version"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db
        rows = []
        for g in db.get_game_cheats(name=query):
            for ch in g.cheats:
                rows.append(((g, ch), [g.game, ch.name, g.region, g.version]))
        return rows

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        g, ch = item
        return [
            ("Game", g.game),
            ("Cheat", ch.name),
            ("Region", g.region),
            ("Version", g.version),
            ("Offsets", [f"{o.opcode} @ {o.offset} = {o.value}" for o in ch.offsets]),
        ]
