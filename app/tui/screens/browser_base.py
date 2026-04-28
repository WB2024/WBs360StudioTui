"""Reusable two-pane browser screen base."""
from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

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
        Binding("l", "toggle_library", "Library Filter", show=True),
        Binding("i", "install", "Install", show=True),
        Binding("d", "download", "Download", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    SEARCH_PLACEHOLDER = "Search..."

    def __init__(self, title_id_filter: str | None = None, **kwargs: Any) -> None:
        """Args:
            title_id_filter: When set (e.g. from My Library drill-down), restrict
                results to this single Title ID.  Overrides the library-only toggle.
        """
        super().__init__(**kwargs)
        self._title_id_filter: str | None = title_id_filter.upper() if title_id_filter else None
        self._library_only: bool = False

    @property
    def active_library_ids(self) -> set[str] | None:
        """Return the set of Title IDs to filter by, or None to show all.

        Priority: explicit title_id_filter > library_only toggle > None.
        """
        if self._title_id_filter:
            return {self._title_id_filter}
        if self._library_only:
            library: dict[str, str] = getattr(self.app, "library", {})
            if library:
                return set(library.keys())
        return None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Horizontal(id="filter_bar"):
                    yield Input(placeholder=self.SEARCH_PLACEHOLDER, id="search_input")
                    yield Button("Library", id="lib_filter_btn", classes="lib_filter_off")
                yield ModTable(id="mod_table")
            with Vertical(id="browser_right"):
                yield ModDetail(id="detail_pane")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.TITLE
        # When opened with a title_id_filter, seed the search bar for clarity.
        if self._title_id_filter:
            self.query_one("#search_input", Input).value = self._title_id_filter
        self._refresh_table()
        self._sync_conn_bar()
        self._update_lib_btn()

    def _sync_conn_bar(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    def _update_lib_btn(self) -> None:
        btn = self.query_one("#lib_filter_btn", Button)
        if self._title_id_filter:
            btn.label = f"Game: {self._title_id_filter}"
            btn.remove_class("lib_filter_off")
            btn.add_class("lib_filter_on")
        elif self._library_only:
            btn.label = "Library ✓"
            btn.remove_class("lib_filter_off")
            btn.add_class("lib_filter_on")
        else:
            btn.label = "Library"
            btn.remove_class("lib_filter_on")
            btn.add_class("lib_filter_off")

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
        # When a title_id_filter is set, clear the query so the filter does the work.
        if self._title_id_filter:
            query = ""
        table = self.query_one("#mod_table", ModTable)
        try:
            rows = self.get_rows(query)
        except Exception as e:
            self.query_one("#status_bar", StatusBar).set_text(f"Error: {e}")
            return
        table.populate(self.get_columns(), rows)
        lib_note = " [library filter]" if self.active_library_ids else ""
        self.query_one("#status_bar", StatusBar).set_text(f"{len(rows)} item(s){lib_note}")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._pending = getattr(self, "_pending", 0) + 1
            current = self._pending
            self.set_timer(0.3, lambda: self._maybe_refresh(current))

    def _maybe_refresh(self, token: int) -> None:
        if token == getattr(self, "_pending", 0):
            self._refresh_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "lib_filter_btn" and not self._title_id_filter:
            self.action_toggle_library()

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

    def action_toggle_library(self) -> None:
        """Toggle library-only filter (show only items for games in your library)."""
        if self._title_id_filter:
            return  # fixed filter from Library drill-down; can't toggle
        self._library_only = not self._library_only
        self._update_lib_btn()
        self._refresh_table()

    def action_install(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if item is None:
            return
        self.app.run_worker(run_install_flow(self.app, item), exclusive=False)

    def action_download(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if item is None:
            return
        self.app.run_worker(run_install_flow(self.app, item), exclusive=False)

