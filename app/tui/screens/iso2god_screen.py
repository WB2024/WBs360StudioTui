"""ISO to GOD conversion screen.

This screen lets the user:
1. Browse locally-stored Xbox 360 ISO files
2. Select one and convert it to GOD format using the iso2god binary
3. Optionally follow up with Transfer Games to push to console

The iso2god binary is downloaded automatically on first use from GitHub releases.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

import app.core.iso2god as iso2god_core
from app.core.iso_scanner import IsoGameItem, scan_iso_path
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.mod_detail import ModDetail
from app.tui.widgets.mod_table import ModTable
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


class Iso2GodScreen(Screen):
    """Browse ISOs and convert them to GOD format."""

    TITLE = "ISO → GOD Converter"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("c", "convert", "Convert", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._games: list[IsoGameItem] = []
        self._pending: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Horizontal(id="filter_bar"):
                    yield Input(placeholder="Search by game name...", id="search_input")
                    yield Button("Convert to GOD", id="convert_btn", variant="success")
                    yield Button("Get Binary", id="dl_binary_btn", variant="primary")
                yield ModTable(id="mod_table")
            with Vertical(id="browser_right"):
                yield ModDetail(id="detail_pane")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_games()
        self._refresh_table("")
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self._update_binary_btn()

    def _update_binary_btn(self) -> None:
        btn = self.query_one("#dl_binary_btn", Button)
        if iso2god_core.binary_exists():
            btn.label = f"Binary: Ready ({iso2god_core.ISO2GOD_VERSION})"
            btn.variant = "default"
        else:
            btn.label = "Download Binary"
            btn.variant = "primary"

    def _load_games(self) -> None:
        iso_path = self.app.settings.local_iso_path  # type: ignore[attr-defined]
        self._games = scan_iso_path(iso_path) if iso_path else []

    def _refresh_table(self, query: str) -> None:
        q = query.lower()
        filtered = [g for g in self._games if not q or q in g.name.lower()]

        columns = ["Game Name", "Size (GB)", "ISO Path"]
        rows: list[tuple[Any, list[str]]] = [
            (g, [g.name, f"{g.size_gb:.2f}", str(g.iso_path)])
            for g in filtered
        ]

        self.query_one("#mod_table", ModTable).populate(columns, rows)

        if not self.app.settings.local_iso_path:  # type: ignore[attr-defined]
            status = "No Local ISO Path set — configure it in Settings"
        else:
            status = f"{len(filtered)} ISO(s)"
            if len(filtered) != len(self._games):
                status += f" of {len(self._games)}"
        self.query_one("#status_bar", StatusBar).set_text(status)

    # --- Events ---

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._pending += 1
            current = self._pending
            self.set_timer(0.3, lambda: self._maybe_refresh(current))

    def _maybe_refresh(self, token: int) -> None:
        if token == self._pending:
            self._refresh_table(self.query_one("#search_input", Input).value.strip())

    def on_data_table_row_highlighted(self, event) -> None:
        table = self.query_one("#mod_table", ModTable)
        item = table.get_item(event.row_key.value if event.row_key else None)
        if not isinstance(item, IsoGameItem):
            return

        god_path = self.app.settings.local_god_path or "(not set — configure in Settings)"  # type: ignore[attr-defined]
        binary_status = (
            f"Ready ({iso2god_core.ISO2GOD_VERSION})"
            if iso2god_core.binary_exists()
            else "Not downloaded — click Get Binary"
        )

        fields: list[tuple[str, Any]] = [
            ("Game Name", item.name),
            ("ISO File", str(item.iso_path)),
            ("Size", f"{item.size_gb:.2f} GB"),
            ("Output Folder", god_path),
            ("Binary", binary_status),
        ]
        self.query_one("#detail_pane", ModDetail).show_item(fields)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "convert_btn":
            self.action_convert()
        elif event.button.id == "dl_binary_btn":
            self.app.run_worker(self._download_binary_worker(), exclusive=False)

    # --- Actions ---

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_refresh(self) -> None:
        self._load_games()
        self._refresh_table(self.query_one("#search_input", Input).value.strip())

    def action_quit(self) -> None:
        self.app.exit()

    def action_convert(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if not isinstance(item, IsoGameItem):
            self.query_one("#status_bar", StatusBar).set_text("Select an ISO first")
            return
        self.app.run_worker(self._convert_worker(item), exclusive=True)

    # --- Workers ---

    async def _download_binary_worker(self) -> None:
        if iso2god_core.binary_exists():
            self.query_one("#status_bar", StatusBar).set_text(
                f"Binary already present ({iso2god_core.ISO2GOD_VERSION})"
            )
            return

        modal = ProgressModal("Downloading iso2god binary...")
        await self.app.push_screen(modal)

        def _cb(received: int, total: int) -> None:
            modal.set_stage("Downloading...", received, total if total else received)

        try:
            path = await iso2god_core.download_binary(progress=_cb)
            modal.set_done(f"Binary saved to {path}", success=True)
            self.call_after_refresh(self._update_binary_btn)
        except Exception as e:
            modal.set_done(f"Download failed: {e}", success=False)

    async def _convert_worker(self, game: IsoGameItem) -> None:
        god_path = self.app.settings.local_god_path  # type: ignore[attr-defined]
        if not god_path:
            self.query_one("#status_bar", StatusBar).set_text(
                "Set a Local GOD Path in Settings first"
            )
            return

        if not iso2god_core.binary_exists():
            self.query_one("#status_bar", StatusBar).set_text(
                "iso2god binary not found — click Get Binary"
            )
            return

        binary = iso2god_core.binary_path()

        # Game name folder inside god_path (mirrors the existing GOD structure)
        dest_dir = Path(god_path) / game.name

        modal = ProgressModal(f"Converting: {game.name}")
        await self.app.push_screen(modal)
        modal.set_stage("Starting conversion...", 0, 0)

        def _on_progress(prog: iso2god_core.ConversionProgress) -> None:
            if prog.parts_total > 0:
                stage_label = f"Writing parts ({prog.stage})..."
                modal.set_stage(stage_label, prog.parts_done, prog.parts_total)
            else:
                modal.set_stage(prog.stage.capitalize() + "...", 0, 0)

            if prog.game_name:
                modal.set_detail(f"{prog.game_name} [{prog.title_id}]")

        try:
            final = await iso2god_core.convert_iso(
                iso_path=game.iso_path,
                dest_dir=dest_dir,
                binary=binary,
                num_threads=1,
                trim=True,
                on_progress=_on_progress,
            )
            msg = f"Done! {final.game_name or game.name} → {dest_dir}"
            if final.title_id:
                msg += f"  (TitleID: {final.title_id})"
            modal.set_done(msg, success=True)
            # Reload GOD games in the app if possible
        except iso2god_core.Iso2GodError as e:
            modal.set_done(f"Conversion failed: {e}", success=False)
        except Exception as e:
            modal.set_done(f"Unexpected error: {e}", success=False)
