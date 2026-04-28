from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class GamePatchesScreen(BrowserScreen):
    TITLE = "Game Patches"
    SEARCH_PLACEHOLDER = "Search patches..."

    BINDINGS = [b for b in BrowserScreen.BINDINGS if getattr(b, "key", "") not in ("i", "d")]

    def get_columns(self) -> list[str]:
        return ["Game", "Title ID", "Patches", "Source"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db
        rows = []
        for p in db.get_game_patches(name=query):
            rows.append((p, [p.title_name or p.title_id, p.title_id, str(len(p.patches)), p.source_file]))
        return rows

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        return [
            ("Game", item.title_name),
            ("Title ID", item.title_id),
            ("Hash", item.hash),
            ("Source File", item.source_file),
            ("Patches", [f"{e.name} ({'on' if e.is_enabled else 'off'}) — {e.author}" for e in item.patches]),
        ]
