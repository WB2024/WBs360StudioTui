from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class GameSavesScreen(BrowserScreen):
    TITLE = "Game Saves (Xbox 360)"
    SEARCH_PLACEHOLDER = "Search saves..."

    def get_columns(self) -> list[str]:
        return ["Name", "Game", "Region", "Version"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db
        items = db.get_game_saves(name=query)
        return [(s, [s.name, db.resolve_category_name(s.category_id), s.region, s.version]) for s in items]

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        db = self.app.db
        return [
            ("Name", item.name),
            ("Game", db.resolve_category_name(item.category_id)),
            ("Author", item.created_by),
            ("Region", item.region),
            ("Version", item.version),
            ("Description", item.description),
            ("Files", [d.name for d in item.download_files]),
            ("Install Paths", [p for d in item.download_files for p in d.install_paths]),
        ]
