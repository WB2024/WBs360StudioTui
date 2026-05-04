"""Torrent picker — list .torrent files in the bundled Torrent/ folder."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from app.core.torrent_decoder import (
    DecodedTorrent,
    decode_torrent,
    format_size,
    list_torrent_files,
)
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar

# Bundled folder at repo root.  app/tui/screens/torrent_picker.py → 4 levels up.
_TORRENT_DIR = Path(__file__).parent.parent.parent.parent / "Torrent"


class TorrentPickerScreen(Screen):
    TITLE = "Game Torrents"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("enter", "open", "Open", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._files: list[Path] = []
        self._decoded: dict[str, DecodedTorrent] = {}
        self._current: DecodedTorrent | None = None

    def _torrent_dir(self) -> Path:
        """Return the configured torrent folder, falling back to the bundled Torrent/ dir."""
        settings = getattr(self.app, "settings", None)
        configured = getattr(settings, "torrent_folder", "") if settings else ""
        return Path(configured) if configured else _TORRENT_DIR

    # ── layout ──
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="tor_toolbar"):
            yield Static(f"Folder: {_TORRENT_DIR}", id="tor_folder", classes="muted")
            yield Button("Refresh [R]", id="tor_refresh")
            yield Button("Open [Enter]", id="tor_open", variant="primary")
        with Horizontal(id="tor_main"):
            yield DataTable(id="tor_table", cursor_type="row")
            with Vertical(id="tor_detail"):
                yield Static("[b]Select a torrent to view details[/b]", id="tor_detail_text")
        yield StatusBar(id="status_bar")
        yield Footer()

    # ── lifecycle ──
    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

        table = self.query_one("#tor_table", DataTable)
        table.add_columns("File", "Name", "Files", "Size")

        _dir = self._torrent_dir()
        _dir.mkdir(parents=True, exist_ok=True)
        self.query_one("#tor_folder", Static).update(f"Folder: {_dir}")
        self._refresh_table()

    # ── helpers ──
    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(msg)
        except Exception:
            pass

    def _refresh_table(self) -> None:
        _dir = self._torrent_dir()
        self._files = list_torrent_files(_dir)
        self._decoded = {}
        table = self.query_one("#tor_table", DataTable)
        table.clear()

        if not self._files:
            self._set_status(
                f"No .torrent files found in {_dir}. "
                "Drop .torrent files into that folder and press R."
            )
            self.query_one("#tor_detail_text", Static).update(
                "[dim]No torrents loaded.[/dim]"
            )
            return

        for f in self._files:
            # Try a lightweight decode for size/file count; on failure show errors.
            try:
                d = decode_torrent(f)
                self._decoded[str(f)] = d
                table.add_row(
                    f.name,
                    d.name,
                    str(d.file_count),
                    format_size(d.total_size),
                    key=str(f),
                )
            except Exception as exc:
                table.add_row(f.name, f"[red]decode error[/red]", "-", "-", key=str(f))
                self._set_status(f"Failed to decode {f.name}: {exc}")

        self._set_status(
            f"Loaded {len(self._decoded)} torrent(s) from {_TORRENT_DIR}"
        )

    def _show_detail(self, torrent: DecodedTorrent) -> None:
        self._current = torrent
        trackers = "\n  ".join(torrent.trackers[:5]) if torrent.trackers else "(none)"
        if len(torrent.trackers) > 5:
            trackers += f"\n  … and {len(torrent.trackers) - 5} more"
        text = (
            f"[b cyan]{torrent.name}[/b cyan]\n\n"
            f"[b]Info hash:[/b] {torrent.info_hash}\n"
            f"[b]Files:[/b]     {torrent.file_count}\n"
            f"[b]Size:[/b]      {format_size(torrent.total_size)}\n"
            f"[b]Source:[/b]    {torrent.source_path}\n"
        )
        if torrent.comment:
            text += f"[b]Comment:[/b]   {torrent.comment}\n"
        if torrent.created_by:
            text += f"[b]Creator:[/b]   {torrent.created_by}\n"
        text += f"[b]Trackers:[/b]\n  {trackers}\n"
        self.query_one("#tor_detail_text", Static).update(text)

    # ── events ──
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value) if event.row_key else None
        if key and key in self._decoded:
            self._show_detail(self._decoded[key])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value) if event.row_key else None
        if key and key in self._decoded:
            self._open(self._decoded[key])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tor_refresh":
            self._refresh_table()
        elif event.button.id == "tor_open" and self._current:
            self._open(self._current)

    def _open(self, torrent: DecodedTorrent) -> None:
        from app.tui.screens.torrent_select import TorrentSelectScreen
        self.app.push_screen(TorrentSelectScreen(torrent))

    # ── actions ──
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refresh_table()

    def action_open(self) -> None:
        if self._current:
            self._open(self._current)

    def action_quit(self) -> None:
        self.app.exit()
