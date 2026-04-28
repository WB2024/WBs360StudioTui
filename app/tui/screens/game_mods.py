from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class GameModsScreen(BrowserScreen):
    TITLE = "Game Mods"
    SEARCH_PLACEHOLDER = "Search mods or games..."

    def get_columns(self) -> list[str]:
        return ["Name", "Game", "Version", "Author", "Type", "Region"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db  # type: ignore[attr-defined]
        items = db.get_game_mods(name=query)
        return [(m, [m.name, db.resolve_category_name(m.category_id), m.version, m.created_by, m.mod_type, m.region]) for m in items]

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        db = self.app.db  # type: ignore[attr-defined]
        return [
            ("Name", item.name),
            ("Game", db.resolve_category_name(item.category_id)),
            ("Author", item.created_by),
            ("Version", item.version),
            ("Mod Type", item.mod_type),
            ("Region", item.region),
            ("Firmware", item.firmware_type),
            ("Description", item.description),
            ("Files", [f"{d.name}" for d in item.download_files]),
            ("Install Paths", [p for d in item.download_files for p in d.install_paths]),
        ]
