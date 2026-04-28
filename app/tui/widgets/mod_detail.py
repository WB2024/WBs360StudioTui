"""Detail pane displaying selected item info."""
from __future__ import annotations

from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Static


class ModDetail(VerticalScroll):
    DEFAULT_CSS = ""

    def compose(self):
        yield Static("Select an item to view details.", id="detail_content")

    def show_text(self, markup: str) -> None:
        self.query_one("#detail_content", Static).update(markup)

    def show_item(self, fields: list[tuple[str, Any]]) -> None:
        lines = []
        for label, value in fields:
            if value is None or value == "":
                continue
            if isinstance(value, list):
                value = "\n  - " + "\n  - ".join(str(v) for v in value) if value else ""
            lines.append(f"[b cyan]{label}:[/b cyan] {value}")
        self.show_text("\n\n".join(lines) if lines else "No details available.")
