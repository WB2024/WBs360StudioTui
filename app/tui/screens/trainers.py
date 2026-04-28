from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class TrainersScreen(BrowserScreen):
    TITLE = "Trainers"
    SEARCH_PLACEHOLDER = "Search by game / Title ID / trainer name..."

    def get_columns(self) -> list[str]:
        return ["Game Title", "Title ID", "Trainer", "Type", "Last Updated"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db
        lib_ids = self.active_library_ids
        rows = []
        for game in db.get_trainers(name=query, source=self.active_source_filter):
            if lib_ids is not None and game.title_id.upper() not in lib_ids:
                continue
            game_title = db.resolve_game_title(game.title_id)
            for t in game.trainers:
                tag = "[A]" if t.trainer_type == "Aurora" else "[X]"
                src_tag = " [L]" if t.source == "local" else ""
                rows.append((t, [game_title, game.title_id, f"{tag} {t.name}{src_tag}", t.trainer_type, t.last_updated]))
        return rows

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        fields = [
            ("Trainer", item.name),
            ("Type", item.trainer_type),
            ("Source", "Local" if item.source == "local" else "Online (Arisen Studio)"),
            ("Requires", "Aurora Dashboard" if item.trainer_type == "Aurora" else "XBDM Debug Monitor"),
            ("Last Updated", item.last_updated or "N/A"),
            ("Install Paths", item.install_paths),
        ]
        if item.source == "local":
            fields.append(("Local File", item.local_path))
        else:
            fields.append(("Download URL", item.url))
        return fields
