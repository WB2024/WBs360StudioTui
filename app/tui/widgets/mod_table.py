"""Sortable mod table widget."""
from __future__ import annotations

from typing import Any

from textual.widgets import DataTable


class ModTable(DataTable):
    """DataTable wrapper that tracks underlying items by row key."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._items: dict[str, Any] = {}

    def populate(self, columns: list[str], rows: list[tuple[Any, list[str]]]) -> None:
        """rows: list of (item, [cell strings])."""
        self.clear(columns=True)
        self._items.clear()
        for col in columns:
            self.add_column(col)
        for idx, (item, cells) in enumerate(rows):
            key = str(idx)
            self.add_row(*cells, key=key)
            self._items[key] = item

    def get_item(self, row_key: str | None) -> Any | None:
        if row_key is None:
            return None
        return self._items.get(str(row_key))

    def selected_item(self) -> Any | None:
        if self.row_count == 0:
            return None
        try:
            row_key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
        except Exception:
            return None
        return self.get_item(row_key)
