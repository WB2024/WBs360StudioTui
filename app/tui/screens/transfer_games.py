"""Transfer Games screen — list and transfer local GOD-format games to Xbox 360."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

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
# Transfer flow
# ---------------------------------------------------------------------------

async def run_god_transfer_flow(app: Any, game: GodGameItem) -> None:
    """Orchestrate a GOD game transfer: pick method → connect → transfer."""
    method: str | None = await app.push_screen_wait(GodInstallChoiceModal())
    if not method:
        return

    dest_root: str = app.settings.game_install_path or "Hdd:\\Content\\0000000000000000\\"
    installer = app.installer

    if method == "ftp":
        prof = app.settings.default_profile()
        if prof is None:
            prof = await app.push_screen_wait(ConnectionScreen())
            if not prof:
                return

        total_files = game.file_count
        modal = ProgressModal(f"Transferring: {game.name}")
        await app.push_screen(modal)
        client = FtpClient(prof.host, prof.port, prof.username, prof.password)

        def cb(stage: str, cur: int, total: int) -> None:
            modal.set_stage(f"Transferring files...", cur, total)
            modal.set_detail(f"File {cur} of {total}")

        try:
            await client.connect()
            app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")
            result: InstallResult = await installer.install_god_via_ftp(
                game, client, dest_root, progress=cb
            )
            await client.disconnect()
            modal.set_done(result.message, success=result.success)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)
            try:
                await client.disconnect()
            except Exception:
                pass
        return

    if method == "usb":
        usb_root: str | None = await app.push_screen_wait(UsbChoiceModal())
        if not usb_root:
            return

        modal = ProgressModal(f"Copying to USB: {game.name}")
        await app.push_screen(modal)

        def cb(stage: str, cur: int, total: int) -> None:
            modal.set_stage("Copying files...", cur, total)
            modal.set_detail(f"File {cur} of {total}")

        try:
            result = await installer.install_god_via_usb(
                game, usb_root, dest_root, progress=cb
            )
            modal.set_done(result.message, success=result.success)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)


# ---------------------------------------------------------------------------
# Transfer Games screen
# ---------------------------------------------------------------------------

class TransferGamesScreen(Screen):
    """Browse and transfer locally-stored GOD games to the console."""

    TITLE = "Transfer Games"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("i", "transfer", "Transfer", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._games: list[GodGameItem] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Horizontal(id="filter_bar"):
                    yield Input(placeholder="Search by game name or Title ID...", id="search_input")
                    yield Button("Refresh [R]", id="refresh_btn")
                    yield Button("Transfer", id="transfer_btn", variant="primary")
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

    def _load_games(self) -> None:
        god_path = self.app.settings.local_god_path  # type: ignore[attr-defined]
        if not god_path:
            self._games = []
        else:
            self._games = scan_god_path(god_path)

    def _refresh_table(self, query: str) -> None:
        q = query.lower()
        filtered = [
            g for g in self._games
            if not q or q in g.name.lower() or q in g.title_id.lower()
        ]

        columns = ["Game Name", "Title ID", "Content Type", "Files", "Size (GB)"]
        rows: list[tuple[Any, list[str]]] = []
        for g in filtered:
            rows.append((
                g,
                [
                    g.name,
                    g.title_id,
                    g.content_type,
                    str(g.file_count),
                    f"{g.total_size_gb:.2f}",
                ],
            ))

        table = self.query_one("#mod_table", ModTable)
        table.populate(columns, rows)

        if not self.app.settings.local_god_path:  # type: ignore[attr-defined]
            status = "No Local GOD Path set — configure it in Settings"
        else:
            status = f"{len(filtered)} game(s)"
            if len(filtered) != len(self._games):
                status += f" of {len(self._games)}"
        self.query_one("#status_bar", StatusBar).set_text(status)

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

    def action_transfer(self) -> None:
        item = self.query_one("#mod_table", ModTable).selected_item()
        if not isinstance(item, GodGameItem):
            return
        self.app.run_worker(run_god_transfer_flow(self.app, item), exclusive=False)
