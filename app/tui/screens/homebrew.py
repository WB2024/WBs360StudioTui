from __future__ import annotations

from typing import Any

from app.tui.screens.browser_base import BrowserScreen


class HomebrewScreen(BrowserScreen):
    TITLE = "Homebrew"
    SEARCH_PLACEHOLDER = "Search homebrew..."

    def get_columns(self) -> list[str]:
        return ["Name", "Category", "Version", "Author"]

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        db = self.app.db
        lib_ids = self.active_library_ids
        lib_cats = db.library_category_ids(getattr(self.app, 'library', {})) if lib_ids is not None else None
        items = db.get_homebrew(name=query)
        if lib_cats is not None:
            items = [m for m in items if m.category_id in lib_cats]
        return [(m, [m.name, db.resolve_category_name(m.category_id), m.version, m.created_by]) for m in items]

    def get_detail_fields(self, item) -> list[tuple[str, Any]]:
        db = self.app.db
        return [
            ("Name", item.name),
            ("Category", db.resolve_category_name(item.category_id)),
            ("Author", item.created_by),
            ("Version", item.version),
            ("Description", item.description),
            ("Files", [d.name for d in item.download_files]),
            ("Install Paths", [p for d in item.download_files for p in d.install_paths]),
        ]
