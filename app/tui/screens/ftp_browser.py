"""FTP File Browser screen for navigating the Xbox 360 filesystem."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from app.core.ftp_client import FtpClient, FtpConnectionError, FtpTransferError
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FtpEntry:
    """Represents a single file or directory in the remote listing."""
    name: str
    is_dir: bool
    size: int = 0      # bytes; 0 for directories
    modified: str = "" # formatted "YYYY-MM-DD HH:MM" or ""

    @property
    def size_str(self) -> str:
        if self.is_dir:
            return "<DIR>"
        b = self.size
        if b < 1024:
            return f"{b} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        else:
            return f"{b / 1024 ** 3:.2f} GB"

    @property
    def display_name(self) -> str:
        return f"{'📁' if self.is_dir else '📄'} {self.name}"


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class ConfirmDeleteModal(ModalScreen[bool]):
    """Ask for confirmation before deleting a file or directory."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, entry: FtpEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        kind = "folder" if self._entry.is_dir else "file"
        with Vertical(id="confirm_box"):
            yield Static(f"[b red]Delete {kind}?[/b red]", id="confirm_title")
            yield Static(f"[b]{self._entry.name}[/b]", id="confirm_name")
            if self._entry.is_dir:
                yield Static(
                    "[yellow]Warning: directory must be empty or deletion will fail.[/yellow]",
                    id="confirm_warn",
                )
            yield Static("This cannot be undone.", id="confirm_msg")
            with Horizontal(id="confirm_btns"):
                yield Button("Delete", id="confirm_yes", variant="error")
                yield Button("Cancel", id="confirm_no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class RenameModal(ModalScreen[str | None]):
    """Input a new name for a file or directory."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self._current = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename_box"):
            yield Static(f"Rename: [b]{self._current}[/b]", id="rename_title")
            yield Input(value=self._current, id="rename_input")
            with Horizontal(id="rename_btns"):
                yield Button("Rename", id="rename_ok", variant="primary")
                yield Button("Cancel", id="rename_cancel")

    def on_mount(self) -> None:
        self.query_one("#rename_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename_ok":
            name = self.query_one("#rename_input", Input).value.strip()
            self.dismiss(name or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewFolderModal(ModalScreen[str | None]):
    """Prompt for a new folder name to create in the current directory."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="newfolder_box"):
            yield Static("[b]New Folder[/b]", id="newfolder_title")
            yield Input(placeholder="Folder name", id="newfolder_input")
            with Horizontal(id="newfolder_btns"):
                yield Button("Create", id="newfolder_ok", variant="primary")
                yield Button("Cancel", id="newfolder_cancel")

    def on_mount(self) -> None:
        self.query_one("#newfolder_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "newfolder_ok":
            name = self.query_one("#newfolder_input", Input).value.strip()
            self.dismiss(name or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class FtpBrowserScreen(Screen):
    """Navigate the Xbox 360 filesystem over FTP.

    Keys:
      Enter / → — navigate into a directory
      Backspace  — go up one level
      R          — rename selected item
      D          — delete selected item (with confirmation)
      F5         — refresh current directory
      Esc        — go back to main menu (disconnects)
    """

    TITLE = "FTP File Browser"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("backspace", "go_up", "Up", show=True),
        Binding("n", "new_folder", "New Folder", show=True),
        Binding("r", "rename", "Rename", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("f5", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: FtpClient | None = None
        self._path: str = "/"
        self._entries: list[FtpEntry] = []

    # --- Layout ---

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        yield Static("  📂 [b cyan]/[/b cyan]", id="fb_path_bar")
        with Horizontal(id="fb_toolbar"):
            yield Button("↑ Up", id="fb_up_btn")
            yield Button("New Folder [N]", id="fb_newfolder_btn", variant="success")
            yield Button("Rename [R]", id="fb_rename_btn")
            yield Button("Delete [D]", id="fb_delete_btn", variant="error")
            yield Button("Refresh [F5]", id="fb_refresh_btn")
        yield DataTable(id="fb_table", cursor_type="row")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#fb_table", DataTable)
        table.add_columns("Name", "Size", "Modified")
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self._connect_and_list()

    # --- Connection & listing ---

    @work(exclusive=True, exit_on_error=False)
    async def _connect_and_list(self) -> None:
        prof = self.app.settings.default_profile()  # type: ignore[attr-defined]
        if prof is None:
            self._set_status("No connection profile configured — add one in Settings.")
            return
        self._set_status(f"Connecting to {prof.host}:{prof.port}…")
        try:
            self._client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            await self._client.connect()
        except FtpConnectionError as e:
            self._client = None
            self._set_status(f"Connection failed: {e}")
            return
        await self._list_path("/")

    async def _list_path(self, path: str) -> None:
        if not self._client:
            self._set_status("Not connected.")
            return
        self._set_status(f"Loading {path}…")
        try:
            raw = await self._client.list_detail(path)
        except FtpTransferError as e:
            self._set_status(f"Error listing {path}: {e}")
            return

        entries: list[FtpEntry] = []
        for name, is_dir, size, modified in raw:
            # Format: YYYYMMDDHHMMSS → YYYY-MM-DD HH:MM
            if len(modified) >= 12:
                modified = (
                    f"{modified[:4]}-{modified[4:6]}-{modified[6:8]}"
                    f" {modified[8:10]}:{modified[10:12]}"
                )
            else:
                modified = ""
            entries.append(FtpEntry(name=name, is_dir=is_dir, size=size, modified=modified))

        # Dirs first, then files — both alphabetically
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self._entries = entries
        self._path = path
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#fb_table", DataTable)
        table.clear()
        for i, entry in enumerate(self._entries):
            table.add_row(entry.display_name, entry.size_str, entry.modified, key=str(i))
        self.query_one("#fb_path_bar", Static).update(
            f"  📂 [b cyan]{self._path}[/b cyan]"
        )
        self._set_status(f"{len(self._entries)} item(s)  —  {self._path}")

    # --- Helpers ---

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(msg)
        except Exception:
            pass

    def _selected_entry(self) -> FtpEntry | None:
        table = self.query_one("#fb_table", DataTable)
        idx = table.cursor_row
        if 0 <= idx < len(self._entries):
            return self._entries[idx]
        return None

    def _join(self, name: str) -> str:
        return self._path.rstrip("/") + "/" + name

    def _parent_path(self) -> str:
        parts = self._path.rstrip("/").rsplit("/", 1)
        return parts[0] if parts[0] else "/"

    # --- Event handlers ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "fb_up_btn": self.action_go_up,
            "fb_newfolder_btn": self.action_new_folder,
            "fb_rename_btn": self.action_rename,
            "fb_delete_btn": self.action_delete,
            "fb_refresh_btn": self.action_refresh,
        }
        action = mapping.get(event.button.id)
        if action:
            result = action()
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter key: navigate into directories."""
        entry = self._selected_entry()
        if entry and entry.is_dir:
            asyncio.ensure_future(self._list_path(self._join(entry.name)))

    # --- Actions ---

    async def action_back(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.app.pop_screen()

    @work(exclusive=True, exit_on_error=False)
    async def _navigate(self, path: str) -> None:
        """Navigate to a path as a worker (avoids passing coroutine objects to run_worker)."""
        await self._list_path(path)

    async def action_go_up(self) -> None:
        if self._path == "/":
            return
        await self._list_path(self._parent_path())

    async def action_refresh(self) -> None:
        await self._list_path(self._path)

    def action_new_folder(self) -> None:
        self.app.push_screen(
            NewFolderModal(),
            callback=lambda name: self._do_new_folder(name),
        )

    def _do_new_folder(self, name: str | None) -> None:
        if not name:
            return
        asyncio.ensure_future(self._new_folder_worker(name))

    async def _new_folder_worker(self, name: str) -> None:
        if not self._client:
            self._set_status("Not connected.")
            return
        new_path = self._join(name)
        self._set_status(f"Creating folder {name}…")
        try:
            await self._client.make_directory(new_path)
            await self._list_path(self._path)
        except FtpTransferError as e:
            self._set_status(f"Create folder failed: {e}")

    def action_rename(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._set_status("Select an item to rename.")
            return
        self.app.push_screen(
            RenameModal(entry.name),
            callback=lambda new_name: self._do_rename(entry, new_name),
        )

    def _do_rename(self, entry: FtpEntry, new_name: str | None) -> None:
        if not new_name or new_name == entry.name:
            return
        asyncio.ensure_future(self._rename_worker(entry, new_name))

    async def _rename_worker(self, entry: FtpEntry, new_name: str) -> None:
        if not self._client:
            self._set_status("Not connected.")
            return
        self._set_status(f"Renaming {entry.name} → {new_name}…")
        try:
            await self._client.rename(self._join(entry.name), self._join(new_name))
            await self._list_path(self._path)
        except FtpTransferError as e:
            self._set_status(f"Rename failed: {e}")

    def action_delete(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._set_status("Select an item to delete.")
            return
        self.app.push_screen(
            ConfirmDeleteModal(entry),
            callback=lambda confirmed: self._do_delete(entry, confirmed),
        )

    def _do_delete(self, entry: FtpEntry, confirmed: bool) -> None:
        if not confirmed:
            return
        asyncio.ensure_future(self._delete_worker(entry))

    async def _delete_worker(self, entry: FtpEntry) -> None:
        if not self._client:
            self._set_status("Not connected.")
            return
        self._set_status(f"Deleting {entry.name}…")
        try:
            if entry.is_dir:
                await self._client.remove_directory(self._join(entry.name))
            else:
                await self._client.delete_file(self._join(entry.name))
            await self._list_path(self._path)
        except FtpTransferError as e:
            self._set_status(f"Delete failed: {e}")

    def action_quit(self) -> None:
        self.app.exit()
