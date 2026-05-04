"""Title Updates screen — browse and install local Title Update packages."""
from __future__ import annotations

import time
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from app.core.ftp_client import FtpClient
from app.core.installer import InstallResult
from app.core.tu_scanner import scan_local_title_updates
from app.core.usb_manager import UsbManager
from app.models.title_update import TitleUpdateItem
from app.tui.screens.connection import ConnectionScreen
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.mod_detail import ModDetail
from app.tui.widgets.mod_table import ModTable
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(b: int) -> str:
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"


def _drive_from_path(xbox_path: str) -> str:
    """Extract just the drive name (e.g. 'Usb1') from 'Usb1\\Games' or 'Usb1:'."""
    if not xbox_path:
        return "Usb1"
    part = xbox_path.replace("\\", "/").split("/")[0].rstrip(":")
    return part if part else "Usb1"


# ---------------------------------------------------------------------------
# Install-method modal
# ---------------------------------------------------------------------------

class TuInstallChoiceModal(ModalScreen[str | None]):
    """Ask user how to install the Title Update — FTP or USB."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]Install Method[/]")
            yield Button("FTP — send to Xbox 360", id="ic_ftp", variant="primary")
            yield Button("USB — copy to mounted drive", id="ic_usb")
            yield Button("Cancel", id="ic_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m: dict[str, str | None] = {"ic_ftp": "ftp", "ic_usb": "usb", "ic_cancel": None}
        self.dismiss(m.get(event.button.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class UsbChoiceModal(ModalScreen[str | None]):
    """Pick a USB drive to copy to."""

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
# Install flow
# ---------------------------------------------------------------------------

async def run_tu_install_flow(app: Any, tu: TitleUpdateItem) -> None:
    """Orchestrate a Title Update install: pick method → connect → transfer."""
    method: str | None = await app.push_screen_wait(TuInstallChoiceModal())
    if not method:
        return

    # Derive the target drive from the configured game install path
    game_drive = _drive_from_path(app.settings.game_install_path or "Usb1")
    installer = app.installer

    if method == "ftp":
        prof = app.settings.default_profile()
        if prof is None:
            prof = await app.push_screen_wait(ConnectionScreen())
            if not prof:
                return

        modal = ProgressModal(f"Installing TU: {tu.display_name}")
        await app.push_screen(modal)
        client = FtpClient(prof.host, prof.port, prof.username, prof.password)

        _start = time.monotonic()

        def cb(stage: str, cur: int, total: int) -> None:
            elapsed = time.monotonic() - _start
            pct = (cur / total * 100) if total > 0 else 0
            speed = cur / elapsed if elapsed > 1 else 0
            speed_str = f"{_fmt_bytes(int(speed))}/s" if elapsed > 1 else "…"
            modal.set_stage(f"Uploading… {pct:.1f}%", cur, total)
            modal.set_detail(f"{_fmt_bytes(cur)} of {_fmt_bytes(total)}  •  {speed_str}")

        try:
            await client.connect()
            app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")
            result: InstallResult = await installer.install_title_update_via_ftp(
                tu, client, game_drive=game_drive, progress=cb
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

        modal = ProgressModal(f"Copying TU: {tu.display_name}")
        await app.push_screen(modal)

        _start = time.monotonic()

        def cb(stage: str, cur: int, total: int) -> None:
            elapsed = time.monotonic() - _start
            pct = (cur / total * 100) if total > 0 else 0
            speed = cur / elapsed if elapsed > 1 else 0
            speed_str = f"{_fmt_bytes(int(speed))}/s" if elapsed > 1 else "…"
            modal.set_stage(f"Copying… {pct:.1f}%", cur, total)
            modal.set_detail(f"{_fmt_bytes(cur)} of {_fmt_bytes(total)}  •  {speed_str}")

        try:
            result = await installer.install_title_update_via_usb(
                tu, usb_root, game_drive=game_drive, progress=cb
            )
            modal.set_done(result.message, success=result.success)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)


# ---------------------------------------------------------------------------
# Title Updates screen
# ---------------------------------------------------------------------------

class TitleUpdatesScreen(Screen):
    """Browse local Title Updates and install them to the console."""

    TITLE = "Title Updates"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("i", "install", "Install", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._updates: list[TitleUpdateItem] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="browser_layout"):
            with Vertical(id="browser_left"):
                with Horizontal(id="filter_bar"):
                    yield Input(placeholder="Search by name or Title ID…", id="search_input")
                    yield Button("Refresh [R]", id="refresh_btn")
                    yield Button("Install [I]", id="install_btn", variant="primary")
                yield ModTable(id="mod_table")
            with Vertical(id="browser_right"):
                yield ModDetail(id="detail_pane")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_updates()
        self._refresh_table("")
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    # --- Data ---

    def _load_updates(self) -> None:
        settings = self.app.settings  # type: ignore[attr-defined]
        tu_path = getattr(settings, "local_title_updates_path", "") or None
        self._updates = scan_local_title_updates(path=tu_path)

    def _in_library(self, title_id: str) -> bool:
        library: dict[str, str] = getattr(self.app, "library", {})
        return title_id.upper() in library

    def _refresh_table(self, query: str) -> None:
        q = query.lower()
        filtered = [
            u for u in self._updates
            if not q or q in u.display_name.lower() or q in u.title_id.lower()
        ]

        columns = ["Update Name", "Title ID", "Version", "In Library", "Size"]
        rows: list[tuple[Any, list[str]]] = []
        for u in filtered:
            in_lib = "✓ Yes" if self._in_library(u.title_id) else "No"
            rows.append((
                u,
                [
                    u.display_name,
                    u.title_id,
                    u.version_str,
                    in_lib,
                    u.size_str,
                ],
            ))

        table = self.query_one("#mod_table", ModTable)
        table.populate(columns, rows)

        if not self._updates:
            status = "No Title Updates found in LocalTitleUpdates/ — add STFS TU files there"
        else:
            status = f"{len(filtered)} update(s)"
            if len(filtered) != len(self._updates):
                status += f" of {len(self._updates)}"
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

    def on_data_table_row_highlighted(self, event: Any) -> None:
        table = self.query_one("#mod_table", ModTable)
        item = table.get_item(event.row_key.value if event.row_key else None)
        if not isinstance(item, TitleUpdateItem):
            return
        in_lib = "Yes" if self._in_library(item.title_id) else "No"
        fields: list[tuple[str, Any]] = [
            ("Update Name", item.display_name),
            ("Title ID", item.title_id),
            ("Version", item.version_str),
            ("In Console Library", in_lib),
            ("Filename", item.filename),
            ("Size", item.size_str),
            ("Local Path", str(item.local_path)),
            ("Install Destination",
             f"{_drive_from_path(self.app.settings.game_install_path or 'Usb1')}"  # type: ignore[attr-defined]
             f":\\Content\\0000000000000000\\{item.title_id}\\000B0000\\{item.filename}"),
        ]
        self.query_one("#detail_pane", ModDetail).show_item(fields)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install_btn":
            self.action_install()
        elif event.button.id == "refresh_btn":
            self.action_refresh()

    # --- Actions ---

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_refresh(self) -> None:
        self._load_updates()
        self._refresh_table(self.query_one("#search_input", Input).value.strip())

    def action_install(self) -> None:
        table = self.query_one("#mod_table", ModTable)
        item = table.selected_item()
        if not isinstance(item, TitleUpdateItem):
            self.query_one("#status_bar", StatusBar).set_text("Select a Title Update to install.")
            return
        self.app.run_worker(
            run_tu_install_flow(self.app, item),
            exclusive=False,
            exit_on_error=False,
        )

    def action_quit(self) -> None:
        self.app.exit()
