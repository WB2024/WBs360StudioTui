"""Transfer Games screen — list and transfer local GOD-format games to Xbox 360."""
from __future__ import annotations

import time
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static
from textual.widgets.data_table import RowKey

from app.core.ftp_client import FtpClient
from app.core.god_scanner import scan_god_path
from app.core.installer import InstallResult
from app.core.usb_manager import UsbManager
from app.models.god_game import GodGameItem
from app.tui.screens.connection import ConnectionScreen
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.mod_detail import ModDetail
from app.tui.widgets.mod_table import ModTable
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar

# Console-status cell text
_ON_CONSOLE = "[green]✓ On console[/]"
_NOT_ON_CONSOLE = "[yellow]Not on console[/]"
_UNKNOWN = "[dim]Unknown[/]"


def _fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"


# ---------------------------------------------------------------------------
# Install-method modal (FTP / USB only — no Download for local games)
# ---------------------------------------------------------------------------

class GodInstallChoiceModal(ModalScreen[str | None]):
    """Ask user how to transfer the game — FTP or USB."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]Transfer Method[/]")
            yield Button("FTP — to Xbox 360", id="ic_ftp", variant="primary")
            yield Button("USB — to mounted drive", id="ic_usb")
            yield Button("Cancel", id="ic_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m: dict[str, str | None] = {"ic_ftp": "ftp", "ic_usb": "usb", "ic_cancel": None}
        self.dismiss(m.get(event.button.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class UsbChoiceModal(ModalScreen[str | None]):
    """Pick a USB drive to install to."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]Choose USB Drive[/]")
            yield ListView(id="usb_list")
            with Horizontal():
                yield Button("Use Selected", id="usb_ok", variant="primary")
                yield Button("Cancel", id="usb_cancel")

    def on_mount(self) -> None:
        usb = UsbManager()
        drives = usb.detect_drives()
        lv = self.query_one("#usb_list", ListView)
        if not drives:
            lv.append(ListItem(Label("[yellow]No removable drives detected[/]")))
            return
        for d in drives:
            item = ListItem(Label(d.display))
            item.data = d.mount_point  # type: ignore[attr-defined]
            lv.append(item)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "usb_cancel":
            self.dismiss(None)
            return
        lv = self.query_one("#usb_list", ListView)
        if lv.highlighted_child and hasattr(lv.highlighted_child, "data"):
            self.dismiss(lv.highlighted_child.data)  # type: ignore[attr-defined]
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Transfer flow helpers
# ---------------------------------------------------------------------------

async def run_god_transfer_flow(app: Any, game: GodGameItem) -> None:
    """Orchestrate a single GOD game transfer: pick method → connect → transfer.

    Kept for backwards compatibility; delegates to the bulk helper with one game.
    """
    await run_god_bulk_transfer_flow(app, [game])


async def run_god_bulk_transfer_flow(app: Any, games: list[GodGameItem]) -> None:
    """Transfer one or more GOD games sequentially under a single modal.

    For FTP: one connection is opened and reused across all games.
    For USB: files are copied game-by-game to the chosen drive.
    """
    if not games:
        return

    method: str | None = await app.push_screen_wait(GodInstallChoiceModal())
    if not method:
        return

    dest_root: str = app.settings.game_install_path or "Hdd:\\Content\\0000000000000000\\"
    installer = app.installer
    total_games = len(games)

    if method == "ftp":
        prof = app.settings.default_profile()
        if prof is None:
            prof = await app.push_screen_wait(ConnectionScreen())
            if not prof:
                return

        title = f"Transferring {total_games} game(s) via FTP" if total_games > 1 else f"Transferring: {games[0].name}"
        modal = ProgressModal(title)
        await app.push_screen(modal)

        client = FtpClient(prof.host, prof.port, prof.username, prof.password)
        results: list[tuple[str, bool, str]] = []  # (name, success, message)

        try:
            await client.connect()
            app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")

            for idx, game in enumerate(games, 1):
                game_label = f"[{idx}/{total_games}] {game.name}" if total_games > 1 else game.name
                modal.set_stage(f"Starting: {game_label}…", 0, 0)
                modal.set_detail("")

                _start = time.monotonic()

                def cb(stage: str, cur: int, total: int, _label: str = game_label, _t: float = _start) -> None:
                    elapsed = time.monotonic() - _t
                    pct = (cur / total * 100) if total > 0 else 0
                    speed = cur / elapsed if elapsed > 0 else 0
                    speed_str = f"{_fmt_bytes(int(speed))}/s" if elapsed > 1 else "…"
                    modal.set_stage(f"{_label} — {pct:.1f}%", cur, total)
                    modal.set_detail(f"{_fmt_bytes(cur)} of {_fmt_bytes(total)}  •  {speed_str}")

                try:
                    result: InstallResult = await installer.install_god_via_ftp(
                        game, client, dest_root, progress=cb
                    )
                    results.append((game.name, result.success, result.message))
                except Exception as e:
                    results.append((game.name, False, str(e)))

        except Exception as e:
            modal.set_done(f"FTP connection failed: {e}", success=False)
            try:
                await client.disconnect()
            except Exception:
                pass
            return

        try:
            await client.disconnect()
        except Exception:
            pass

        _show_bulk_result(modal, results)
        return

    if method == "usb":
        usb_root: str | None = await app.push_screen_wait(UsbChoiceModal())
        if not usb_root:
            return

        title = f"Copying {total_games} game(s) to USB" if total_games > 1 else f"Copying to USB: {games[0].name}"
        modal = ProgressModal(title)
        await app.push_screen(modal)

        results = []

        for idx, game in enumerate(games, 1):
            game_label = f"[{idx}/{total_games}] {game.name}" if total_games > 1 else game.name
            modal.set_stage(f"Starting: {game_label}…", 0, 0)
            modal.set_detail("")

            _start = time.monotonic()

            def cb(stage: str, cur: int, total: int, _label: str = game_label, _t: float = _start) -> None:
                elapsed = time.monotonic() - _t
                pct = (cur / total * 100) if total > 0 else 0
                speed = cur / elapsed if elapsed > 0 else 0
                speed_str = f"{_fmt_bytes(int(speed))}/s" if elapsed > 1 else "…"
                modal.set_stage(f"{_label} — {pct:.1f}%", cur, total)
                modal.set_detail(f"{_fmt_bytes(cur)} of {_fmt_bytes(total)}  •  {speed_str}")

            try:
                result = await installer.install_god_via_usb(
                    game, usb_root, dest_root, progress=cb
                )
                results.append((game.name, result.success, result.message))
            except Exception as e:
                results.append((game.name, False, str(e)))

        _show_bulk_result(modal, results)


def _show_bulk_result(modal: ProgressModal, results: list[tuple[str, bool, str]]) -> None:
    """Update the modal with a summary of all game transfer results."""
    if not results:
        modal.set_done("Nothing was transferred.", success=False)
        return

    succeeded = [(n, m) for n, ok, m in results if ok]
    failed = [(n, m) for n, ok, m in results if not ok]

    if len(results) == 1:
        name, ok, msg = results[0]
        modal.set_done(msg, success=ok)
        return

    # Bulk summary
    summary_parts = [f"{len(succeeded)}/{len(results)} game(s) transferred successfully."]
    if failed:
        fail_lines = "; ".join(f"{n}: {m}" for n, m in failed[:3])
        if len(failed) > 3:
            fail_lines += f" (+{len(failed) - 3} more)"
        summary_parts.append(f"Failed: {fail_lines}")

    modal.set_done(" ".join(summary_parts), success=len(failed) == 0)


# ---------------------------------------------------------------------------
# Transfer Games screen
# ---------------------------------------------------------------------------

class TransferGamesScreen(Screen):
    """Browse and transfer locally-stored GOD games to the console."""

    TITLE = "Transfer Games"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("a", "select_all", "All", show=True),
        Binding("n", "select_none", "None", show=True),
        Binding("i", "transfer", "Transfer", show=True),
        Binding("s", "sync", "Sync Missing", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._games: list[GodGameItem] = []
        # Tracks which row keys in the current table view are selected for bulk transfer
        self._selected_keys: set[str] = set()
        # Maps row key → GodGameItem for the current table population
        self._row_items: dict[str, GodGameItem] = {}
        # Maps row key → RowKey object (needed for update_cell_at)
        self._row_keys: dict[str, RowKey] = {}
        # Snapshot of the console library at screen-open time {TITLE_ID_UPPER: ftp_path}
        # None = library never loaded (show Unknown); empty dict = loaded but empty
        self._console_library: dict[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Horizontal(id="filter_bar"):
                    yield Input(placeholder="Search by game name or Title ID...", id="search_input")
                    yield Button("Refresh [R]", id="refresh_btn")
                    yield Button("Select All [A]", id="sel_all_btn")
                    yield Button("Select None [N]", id="sel_none_btn")
                    yield Button("Transfer [I]", id="transfer_btn", variant="primary")
                    yield Button("Sync Missing [S]", id="sync_btn", variant="warning")
                yield ModTable(id="mod_table")
            with Vertical(id="browser_right"):
                yield ModDetail(id="detail_pane")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        # Snapshot the console library once so the column is consistent for this session
        raw: dict[str, str] = getattr(self.app, "library", None) or {}
        self._console_library = {k.upper(): v for k, v in raw.items()}
        self._load_games()
        self._refresh_table("")
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    def _load_games(self) -> None:
        god_path = self.app.settings.local_god_path  # type: ignore[attr-defined]
        if not god_path:
            self._games = []
        else:
            self._games = scan_god_path(god_path)

    def _console_status(self, game: GodGameItem) -> str:
        """Return the console-status cell text for *game*."""
        if self._console_library is None:
            return _UNKNOWN
        return _ON_CONSOLE if game.title_id.upper() in self._console_library else _NOT_ON_CONSOLE

    def _refresh_table(self, query: str) -> None:
        q = query.lower()
        filtered = [
            g for g in self._games
            if not q or q in g.name.lower() or q in g.title_id.lower()
        ]

        # Preserve selections that are still in the filtered set (by title_id)
        selected_title_ids = {
            self._row_items[k].title_id
            for k in self._selected_keys
            if k in self._row_items
        }

        self._row_items.clear()
        self._row_keys.clear()
        self._selected_keys.clear()

        columns = ["", "Console", "Game Name", "Title ID", "Type", "Content Type", "Files", "Size (GB)"]
        rows: list[tuple[Any, list[str]]] = []
        for idx, g in enumerate(filtered):
            key = str(idx)
            checked = g.title_id in selected_title_ids
            if checked:
                self._selected_keys.add(key)
            rows.append((
                g,
                [
                    "[green][x][/]" if checked else "[ ]",
                    self._console_status(g),
                    g.name,
                    g.title_id,
                    g.kind,
                    g.content_type,
                    str(g.file_count),
                    f"{g.total_size_gb:.2f}",
                ],
            ))
            self._row_items[key] = g

        table = self.query_one("#mod_table", ModTable)
        table.populate(columns, rows)

        # Capture the RowKey objects now that populate() has added them
        for key in self._row_items:
            self._row_keys[key] = RowKey(key)

        self._update_status(len(filtered))

    def _update_status(self, total_visible: int) -> None:
        if not self.app.settings.local_god_path:  # type: ignore[attr-defined]
            text = "No Local GOD Path set — configure it in Settings"
        else:
            sel = len(self._selected_keys)
            text = f"{total_visible} game(s)"
            if total_visible != len(self._games):
                text += f" of {len(self._games)}"
            # Count missing games across the current visible set
            if self._console_library is not None:
                missing = sum(
                    1 for k, g in self._row_items.items()
                    if g.title_id.upper() not in self._console_library
                )
                if missing:
                    text += f"  •  [yellow]{missing} not on console[/]"
            if sel:
                text += f"  •  {sel} selected"
        self.query_one("#status_bar", StatusBar).set_text(text)

    def _toggle_row_checkbox(self, key: str) -> None:
        """Toggle the selection state of a single row and update its checkbox cell."""
        if key not in self._row_items:
            return
        if key in self._selected_keys:
            self._selected_keys.discard(key)
            check = "[ ]"
        else:
            self._selected_keys.add(key)
            check = "[green][x][/]"

        table = self.query_one("#mod_table", ModTable)
        try:
            row_idx = table.get_row_index(self._row_keys[key])
            table.update_cell_at((row_idx, 0), check)
        except Exception:
            pass

        visible = sum(1 for k in self._row_items)
        self._update_status(visible)

    def _current_row_key(self) -> str | None:
        """Return the string key of the row at the current cursor position."""
        table = self.query_one("#mod_table", ModTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None

    # --- Events ---

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._pending = getattr(self, "_pending", 0) + 1
            current = self._pending
            self.set_timer(0.3, lambda: self._maybe_refresh(current))

    def _maybe_refresh(self, token: int) -> None:
        if token == getattr(self, "_pending", 0):
            self._refresh_table(self.query_one("#search_input", Input).value.strip())

    def on_data_table_row_highlighted(self, event) -> None:
        table = self.query_one("#mod_table", ModTable)
        item = table.get_item(event.row_key.value if event.row_key else None)
        if not isinstance(item, GodGameItem):
            return
        fields: list[tuple[str, Any]] = [
            ("Game Name", item.name),
            ("Title ID", item.title_id),
            ("Content Type", item.content_type),
            ("Container File", item.container_file),
            ("Files to Transfer", item.file_count),
            ("Total Size", f"{item.total_size_gb:.2f} GB"),
            ("Local Path", str(item.local_path)),
            ("Install Destination", self.app.settings.game_install_path or "Hdd:\\Content\\0000000000000000\\"),  # type: ignore[attr-defined]
        ]
        self.query_one("#detail_pane", ModDetail).show_item(fields)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "transfer_btn":
            self.action_transfer()
        elif event.button.id == "refresh_btn":
            self.action_refresh()
        elif event.button.id == "sel_all_btn":
            self.action_select_all()
        elif event.button.id == "sel_none_btn":
            self.action_select_none()
        elif event.button.id == "sync_btn":
            self.action_sync()

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

    def action_toggle_select(self) -> None:
        """Toggle selection on the currently-highlighted row (Space)."""
        key = self._current_row_key()
        if key is not None:
            self._toggle_row_checkbox(key)

    def action_select_all(self) -> None:
        """Mark every visible row as selected."""
        table = self.query_one("#mod_table", ModTable)
        for key in list(self._row_items):
            self._selected_keys.add(key)
            try:
                row_idx = table.get_row_index(self._row_keys[key])
                table.update_cell_at((row_idx, 0), "[green][x][/]")
            except Exception:
                pass
        self._update_status(len(self._row_items))

    def action_select_none(self) -> None:
        """Clear all selections."""
        table = self.query_one("#mod_table", ModTable)
        for key in list(self._selected_keys):
            try:
                row_idx = table.get_row_index(self._row_keys[key])
                table.update_cell_at((row_idx, 0), "[ ]")
            except Exception:
                pass
        self._selected_keys.clear()
        self._update_status(len(self._row_items))

    def action_sync(self) -> None:
        """Bulk-transfer every local game that is not already on the console.

        Requires a library scan to have been run at least once (Library screen → Scan).
        If the library has never been loaded the user is informed and nothing happens.
        """
        if self._console_library is None:
            self.query_one("#status_bar", StatusBar).set_text(
                "Console library not loaded — run a Library Scan first (Library screen → Scan)"
            )
            return

        missing = [
            self._row_items[k]
            for k in sorted(self._row_items, key=lambda k: int(k))
            if self._row_items[k].title_id.upper() not in self._console_library
        ]

        if not missing:
            self.query_one("#status_bar", StatusBar).set_text(
                "All local games are already on the console — nothing to sync."
            )
            return

        self.app.run_worker(
            run_god_bulk_transfer_flow(self.app, missing), exclusive=False
        )

    def action_transfer(self) -> None:
        """Transfer selected games (bulk), or the highlighted game if none selected."""
        if self._selected_keys:
            games = [
                self._row_items[k]
                for k in sorted(self._selected_keys, key=lambda k: int(k))
                if k in self._row_items
            ]
        else:
            # Fall back to single-row transfer (original behaviour)
            item = self.query_one("#mod_table", ModTable).selected_item()
            if not isinstance(item, GodGameItem):
                self.query_one("#status_bar", StatusBar).set_text(
                    "Select at least one game first (Space to select, A to select all)"
                )
                return
            games = [item]

        self.app.run_worker(
            run_god_bulk_transfer_flow(self.app, games), exclusive=False
        )
