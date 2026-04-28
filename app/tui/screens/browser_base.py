"""Reusable two-pane browser screen base."""
from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input

from app.tui.screens.install import run_install_flow
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.mod_detail import ModDetail
from app.tui.widgets.mod_table import ModTable
from app.tui.widgets.status_bar import StatusBar


class BrowserScreen(Screen):
    """Base class for category browsers. Subclasses provide columns/rows/details."""

    TITLE = "Browser"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("i", "install", "Install", show=True),
        Binding("d", "download", "Download", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    SEARCH_PLACEHOLDER = "Search..."

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Vertical(id="filter_bar"):
                    yield Input(placeholder=self.SEARCH_PLACEHOLDER, id="search_input")
                yield ModTable(id="mod_table")
            with Vertical(id="browser_right"):
                yield ModDetail(id="detail_pane")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.TITLE
        self._refresh_table()
        self._sync_conn_bar()

    def _sync_conn_bar(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    # --- Hooks for subclasses ---
    def get_columns(self) -> list[str]:
        raise NotImplementedError

    def get_rows(self, query: str) -> list[tuple[Any, list[str]]]:
        raise NotImplementedError

    def get_detail_fields(self, item: Any) -> list[tuple[str, Any]]:
        raise NotImplementedError

    # --- Internal ---
    def _refresh_table(self) -> None:
        query = self.query_one("#search_input", Input).value.strip()
        table = self.query_one("#mod_table", ModTable)
        try:
            rows = self.get_rows(query)
        except Exception as e:
            self.query_one("#status_bar", StatusBar).set_text(f"Error: {e}")
            return
        table.populate(self.get_columns(), rows)
        self.query_one("#status_bar", StatusBar).set_text(f"{len(rows)} item(s)")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            # Debounce
            self._pending = getattr(self, "_pending", 0) + 1
            current = self._pending
            self.set_timer(0.3, lambda: self._maybe_refresh(current))

    def _maybe_refresh(self, token: int) -> None:
        if token == getattr(self, "_pending", 0):
            self._refresh_table()

    def on_data_table_row_highlighted(self, event) -> None:
        table = self.query_one("#mod_table", ModTable)
        item = table.get_item(event.row_key.value if event.row_key else None)
        if item is None:
            return
        fields = self.get_detail_fields(item)
        self.query_one("#detail_pane", ModDetail).show_item(fields)

    # --- Actions ---
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_refresh(self) -> None:
        self._refresh_table()

    def action_quit(self) -> None:
        self.app.exit()

    def action_install(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if item is None:
            return
        self.app.run_worker(run_install_flow(self.app, item), exclusive=False)

    def action_download(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if item is None:
            return
        # Reuse install flow but route to download — modal asks user
        self.app.run_worker(run_install_flow(self.app, item), exclusive=False)
