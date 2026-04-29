"""Game Directory Tidy-up screen.

Connects to the Xbox 360 over FTP, analyses the configured games directory,
and reorganises it into a consistent folder structure chosen by the user.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from app.core.ftp_client import FtpClient, FtpConnectionError, _ftp_path
from app.core.game_tidy import (
    ALL_FORMATS,
    FORMAT_TITLE_ID,
    FORMAT_NAME_SLASH_TITLE_ID,
    FORMAT_NAME_DASH_TITLE_ID,
    FORMAT_TITLE_ID_DASH_NAME,
    GameDirEntry,
    TidyMove,
    analyse_games_root,
    apply_moves,
    build_plan,
)
from app.core.library_scanner import load_csv_titles
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar

# CSV at repo root — 4 levels up from this file (app/tui/screens/game_tidy.py)
_CSV_PATH = Path(__file__).parent.parent.parent.parent / "gamelist_xbox360.csv"

# Map button IDs → format strings
_FMT_BUTTONS: dict[str, str] = {
    "fmt_0": FORMAT_TITLE_ID,
    "fmt_1": FORMAT_NAME_SLASH_TITLE_ID,
    "fmt_2": FORMAT_NAME_DASH_TITLE_ID,
    "fmt_3": FORMAT_TITLE_ID_DASH_NAME,
}


# ── Confirmation modal ────────────────────────────────────────────────────────

class ConfirmTidyModal(ModalScreen[bool]):
    """Show a summary of planned changes and ask for confirmation."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, to_move: int, already_ok: int, no_id: int) -> None:
        super().__init__()
        self._to_move = to_move
        self._already_ok = already_ok
        self._no_id = no_id

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static("[b]Apply Directory Changes?[/b]", id="confirm_title")
            yield Static(
                f"  {self._to_move} game(s) will be moved / renamed\n"
                f"  {self._already_ok} already in the correct location (untouched)\n"
                f"  {self._no_id} skipped — Title ID could not be identified",
                id="confirm_summary",
            )
            yield Static(
                "[yellow]This will rename and move folders on your Xbox 360.\n"
                "Ensure no games are currently running.[/yellow]",
                id="confirm_warn",
            )
            with Horizontal(id="confirm_btns"):
                yield Button("Apply", id="confirm_yes", variant="error")
                yield Button("Cancel", id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── Main screen ───────────────────────────────────────────────────────────────

class GameTidyScreen(Screen):
    """Analyse and reorganise the Xbox 360 game directory over FTP."""

    TITLE = "Game Directory Tidy-up"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("a", "analyse", "Analyse", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries: list[GameDirEntry] = []
        self._moves: list[TidyMove] = []
        self._format: str = FORMAT_NAME_SLASH_TITLE_ID
        self._client: FtpClient | None = None
        self._csv_titles: dict[str, str] = {}
        self._games_root: str = ""

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="tidy_toolbar"):
            yield Static("Format:", classes="toolbar_label")
            yield Button(FORMAT_TITLE_ID,          id="fmt_0")
            yield Button(FORMAT_NAME_SLASH_TITLE_ID, id="fmt_1", variant="primary")
            yield Button(FORMAT_NAME_DASH_TITLE_ID, id="fmt_2")
            yield Button(FORMAT_TITLE_ID_DASH_NAME, id="fmt_3")
        with Horizontal(id="tidy_actions"):
            yield Button("Analyse [A]", id="analyse_btn", variant="success")
            yield Button("Apply Changes", id="apply_btn", variant="error", disabled=True)
        yield DataTable(id="tidy_table", cursor_type="row")
        yield StatusBar(id="status_bar")
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

        table = self.query_one("#tidy_table", DataTable)
        table.add_columns("Game Name", "Title ID", "Match", "Current Structure", "Planned Action")

        self._csv_titles = load_csv_titles(_CSV_PATH)

        install_path = self.app.settings.game_install_path  # type: ignore[attr-defined]
        if install_path:
            self._games_root = _ftp_path(install_path)
            self._set_status(
                f"Games root: {install_path}  ({self._games_root})  — "
                "press A or click Analyse to scan."
            )
        else:
            self._set_status("Game install path not set — configure it in Settings.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(msg)
        except Exception:
            pass

    def _update_format_buttons(self) -> None:
        for btn_id, fmt in _FMT_BUTTONS.items():
            self.query_one(f"#{btn_id}", Button).variant = (
                "primary" if fmt == self._format else "default"
            )

    def _populate_table(self) -> None:
        table = self.query_one("#tidy_table", DataTable)
        table.clear()

        for move in self._moves:
            e = move.entry

            tid_str = e.title_id or "?"

            if e.match_source == "csv_id":
                match_str = "CSV"
            elif e.match_source == "csv_fuzzy":
                match_str = f"Fuzzy {e.fuzzy_confidence:.0%}"
            elif e.match_source == "structure":
                match_str = "Dir"
            else:
                match_str = "Unknown"

            if e.current_parent_ftp:
                struct_str = "Name/TitleID"
            elif e.title_id and e.folder_label.upper() == e.title_id:
                struct_str = "TitleID"
            else:
                struct_str = "Other"

            table.add_row(
                e.friendly_name or e.folder_label,
                tid_str,
                match_str,
                struct_str,
                move.description,
            )

        to_move = sum(1 for m in self._moves if not m.skipped)
        already_ok = sum(
            1 for m in self._moves if m.skipped and m.entry.title_id is not None
        )
        no_id = sum(
            1 for m in self._moves if m.skipped and m.entry.title_id is None
        )

        self.query_one("#apply_btn", Button).disabled = to_move == 0
        self._set_status(
            f"{len(self._entries)} game(s) found  |  "
            f"{to_move} to move  |  {already_ok} already correct  |  "
            f"{no_id} skipped (no ID)  |  Format: {self._format}"
        )

    def _rebuild_plan(self) -> None:
        if self._entries and self._games_root:
            self._moves = build_plan(self._entries, self._format, self._games_root)
            self._populate_table()

    # ── Events ────────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in _FMT_BUTTONS:
            self._format = _FMT_BUTTONS[event.button.id]
            self._update_format_buttons()
            self._rebuild_plan()
        elif event.button.id == "analyse_btn":
            asyncio.ensure_future(self._do_analyse())
        elif event.button.id == "apply_btn":
            asyncio.ensure_future(self._do_apply())

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    async def action_analyse(self) -> None:
        await self._do_analyse()

    def action_quit(self) -> None:
        self.app.exit()

    # ── Core operations ───────────────────────────────────────────────────────

    async def _ensure_connected(self) -> FtpClient | None:
        """Return a connected FtpClient, (re)connecting if necessary."""
        if self._client and self._client.is_connected:
            return self._client

        prof = self.app.settings.default_profile()  # type: ignore[attr-defined]
        if prof is None:
            self._set_status("No connection profile configured — add one in Settings.")
            return None

        self._set_status(f"Connecting to {prof.host}:{prof.port}…")
        try:
            client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            await client.connect()
            self._client = client
            bar = self.query_one("#conn_bar", ConnectionBar)
            bar.set_status(connected=True, text=f"{prof.host}:{prof.port}")
            return client
        except FtpConnectionError as exc:
            self._set_status(f"Connection failed: {exc}")
            return None

    async def _do_analyse(self) -> None:
        install_path = self.app.settings.game_install_path  # type: ignore[attr-defined]
        if not install_path:
            self._set_status("Game install path not configured — set it in Settings.")
            return

        self._games_root = _ftp_path(install_path)

        self.query_one("#analyse_btn", Button).disabled = True
        self.query_one("#apply_btn", Button).disabled = True
        self._set_status(f"Scanning {self._games_root}…")

        try:
            client = await self._ensure_connected()
            if client is None:
                return

            self._entries = await analyse_games_root(
                client,
                self._games_root,
                self._csv_titles,
                progress_cb=self._set_status,
            )
            self._moves = build_plan(self._entries, self._format, self._games_root)
            self._populate_table()

        except Exception as exc:
            self._set_status(f"Error during analysis: {exc}")
        finally:
            self.query_one("#analyse_btn", Button).disabled = False

    async def _do_apply(self) -> None:
        if not self._moves:
            return

        to_move = sum(1 for m in self._moves if not m.skipped)
        already_ok = sum(
            1 for m in self._moves if m.skipped and m.entry.title_id is not None
        )
        no_id = sum(
            1 for m in self._moves if m.skipped and m.entry.title_id is None
        )

        confirmed = await self.app.push_screen_wait(
            ConfirmTidyModal(to_move, already_ok, no_id)
        )
        if not confirmed:
            return

        self.query_one("#analyse_btn", Button).disabled = True
        self.query_one("#apply_btn", Button).disabled = True
        self._set_status("Applying changes…")

        try:
            client = await self._ensure_connected()
            if client is None:
                return

            results = await apply_moves(
                client,
                self._moves,
                progress_cb=self._set_status,
            )

            success = sum(1 for _, ok, _ in results if ok)
            failed = sum(1 for _, ok, _ in results if not ok)

            status = f"Done — {success} moved successfully"
            if failed:
                status += f", {failed} failed"
            status += "  |  Press A to re-analyse."
            self._set_status(status)

            # Clear cached state so the user must re-analyse after applying.
            self._entries = []
            self._moves = []
            self.query_one("#tidy_table", DataTable).clear()

        except Exception as exc:
            self._set_status(f"Error applying changes: {exc}")
        finally:
            self.query_one("#analyse_btn", Button).disabled = False
