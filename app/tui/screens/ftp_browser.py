"""FTP File Manager — dual-pane (FileZilla-style) for Xbox 360 ↔ Local transfers."""
from __future__ import annotations

import asyncio
import datetime
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from app.core.ftp_client import FtpClient, FtpConnectionError, FtpTransferError
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


# ---------------------------------------------------------------------------
# Shared data model
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    """A file/directory entry for either pane (local or remote)."""
    name: str
    is_dir: bool
    size: int = 0       # bytes; 0 for directories
    modified: str = ""  # formatted "YYYY-MM-DD HH:MM" or ""

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

    def __init__(self, entry: FileEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        kind = "folder" if self._entry.is_dir else "file"
        with Vertical(id="confirm_box"):
            yield Static(f"[b red]Delete {kind}?[/b red]", id="confirm_title")
            yield Static(f"[b]{self._entry.name}[/b]", id="confirm_name")
            if self._entry.is_dir:
                yield Static(
                    "[yellow]All contents will be deleted recursively.[/yellow]",
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
    """Dual-pane file manager: local filesystem (left) vs Xbox 360 console (right).

    Keys:
      Tab        — switch active pane
      Enter      — navigate into a directory
      Backspace  — go up one level in active pane
      T          — transfer selected file (copy to the other pane)
      C          — copy selected item to clipboard
      X          — cut selected item to clipboard
      V          — paste clipboard item into active pane
      N          — new folder in active pane
      R          — rename selected item in active pane
      D          — delete selected item in active pane (with confirmation)
      F5         — refresh active pane
      Esc        — go back to main menu
    """

    TITLE = "File Manager"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("tab", "switch_pane", "Switch Pane", show=True),
        Binding("backspace", "go_up", "Up", show=True),
        Binding("t", "transfer", "Transfer ⇄", show=True),
        Binding("c", "copy", "Copy", show=True),
        Binding("x", "cut", "Cut", show=True),
        Binding("v", "paste", "Paste", show=True),
        Binding("n", "new_folder", "New Folder", show=True),
        Binding("r", "rename", "Rename", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("f5", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: FtpClient | None = None

        # Remote pane state
        self._remote_path: str = "/"
        self._remote_entries: list[FileEntry] = []

        # Local pane state (start in user's home directory)
        self._local_path: Path = Path.home()
        self._local_entries: list[FileEntry] = []

        # "local" or "remote"
        self._active: str = "local"

        # Clipboard state for copy/cut/paste
        self._clipboard_entry: FileEntry | None = None
        self._clipboard_src_path: Path | str | None = None  # Path for local, str for remote
        self._clipboard_pane: str | None = None  # "local" or "remote"
        self._clipboard_op: str | None = None    # "copy" or "cut"

    # -------------------------------------------------------------------------
    # Layout
    # -------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="fm_panels"):
            with Vertical(id="fm_local_panel", classes="panel-active"):
                yield Static("", id="fm_local_path_bar", classes="panel-path-bar")
                yield DataTable(id="fm_local_table", cursor_type="row")
            with Vertical(id="fm_remote_panel"):
                yield Static("  🎮 [dim]Connecting…[/dim]", id="fm_remote_path_bar", classes="panel-path-bar")
                yield DataTable(id="fm_remote_table", cursor_type="row")
        with Horizontal(id="fm_toolbar"):
            yield Button("↑ Up", id="fm_up_btn")
            yield Button("New Folder [N]", id="fm_newfolder_btn", variant="success")
            yield Button("Rename [R]", id="fm_rename_btn")
            yield Button("Copy [C]", id="fm_copy_btn")
            yield Button("Cut [X]", id="fm_cut_btn", variant="warning")
            yield Button("Paste [V]", id="fm_paste_btn", variant="primary")
            yield Button("Delete [D]", id="fm_delete_btn", variant="error")
            yield Button("⇄ Transfer [T]", id="fm_transfer_btn", variant="primary")
            yield Button("Refresh [F5]", id="fm_refresh_btn")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        for tbl_id in ("fm_local_table", "fm_remote_table"):
            tbl = self.query_one(f"#{tbl_id}", DataTable)
            tbl.add_columns("Name", "Size", "Modified")

        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

        self._refresh_local()
        self._connect_and_list()
        self.query_one("#fm_local_table", DataTable).focus()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(msg)
        except Exception:
            pass

    def _local_selected(self) -> FileEntry | None:
        tbl = self.query_one("#fm_local_table", DataTable)
        idx = tbl.cursor_row
        if 0 <= idx < len(self._local_entries):
            return self._local_entries[idx]
        return None

    def _remote_selected(self) -> FileEntry | None:
        tbl = self.query_one("#fm_remote_table", DataTable)
        idx = tbl.cursor_row
        if 0 <= idx < len(self._remote_entries):
            return self._remote_entries[idx]
        return None

    def _active_selected(self) -> FileEntry | None:
        return self._local_selected() if self._active == "local" else self._remote_selected()

    def _join_remote(self, name: str) -> str:
        return self._remote_path.rstrip("/") + "/" + name

    def _remote_parent(self) -> str:
        parts = self._remote_path.rstrip("/").rsplit("/", 1)
        return parts[0] if parts[0] else "/"

    def _update_pane_styles(self) -> None:
        local_panel = self.query_one("#fm_local_panel")
        remote_panel = self.query_one("#fm_remote_panel")
        if self._active == "local":
            local_panel.add_class("panel-active")
            remote_panel.remove_class("panel-active")
        else:
            remote_panel.add_class("panel-active")
            local_panel.remove_class("panel-active")

    # -------------------------------------------------------------------------
    # Local pane
    # -------------------------------------------------------------------------

    def _refresh_local(self) -> None:
        self._local_entries = self._build_local_entries(self._local_path)
        self._populate_pane("local")

    def _build_local_entries(self, path: Path) -> list[FileEntry]:
        entries: list[FileEntry] = []
        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return []
        for item in items:
            try:
                stat = item.stat()
                size = stat.st_size if item.is_file() else 0
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size = 0
                modified = ""
            entries.append(FileEntry(name=item.name, is_dir=item.is_dir(), size=size, modified=modified))
        return entries

    # -------------------------------------------------------------------------
    # Remote pane
    # -------------------------------------------------------------------------

    @work(exclusive=True, exit_on_error=False)
    async def _connect_and_list(self) -> None:
        prof = self.app.settings.default_profile()  # type: ignore[attr-defined]
        if prof is None:
            self._set_status("No connection profile — add one in Settings.")
            return
        self._set_status(f"Connecting to {prof.host}:{prof.port}…")
        try:
            self._client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            await self._client.connect()
        except FtpConnectionError as e:
            self._client = None
            self._set_status(f"Connection failed: {e}")
            self.query_one("#fm_remote_path_bar", Static).update(
                "  🎮 [red]Not connected[/red]"
            )
            return
        await self._list_remote("/")

    async def _list_remote(self, path: str) -> None:
        if not self._client:
            self._set_status("Not connected to console.")
            return
        self._set_status(f"Loading {path}…")
        try:
            raw = await self._client.list_detail(path)
        except FtpTransferError as e:
            self._set_status(f"Error listing {path}: {e}")
            return

        entries: list[FileEntry] = []
        for name, is_dir, size, modified in raw:
            # Format: YYYYMMDDHHMMSS → YYYY-MM-DD HH:MM
            if len(modified) >= 12:
                modified = (
                    f"{modified[:4]}-{modified[4:6]}-{modified[6:8]}"
                    f" {modified[8:10]}:{modified[10:12]}"
                )
            else:
                modified = ""
            entries.append(FileEntry(name=name, is_dir=is_dir, size=size, modified=modified))

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self._remote_entries = entries
        self._remote_path = path
        self._populate_pane("remote")

    # -------------------------------------------------------------------------
    # Table population
    # -------------------------------------------------------------------------

    def _populate_pane(self, pane: str) -> None:
        if pane == "local":
            tbl = self.query_one("#fm_local_table", DataTable)
            entries = self._local_entries
            self.query_one("#fm_local_path_bar", Static).update(
                f"  💻 [b cyan]{self._local_path}[/b cyan]"
            )
            label = f"Local: {self._local_path}"
        else:
            tbl = self.query_one("#fm_remote_table", DataTable)
            entries = self._remote_entries
            self.query_one("#fm_remote_path_bar", Static).update(
                f"  🎮 [b yellow]{self._remote_path}[/b yellow]"
            )
            label = f"Console: {self._remote_path}"

        tbl.clear()
        for i, entry in enumerate(entries):
            tbl.add_row(entry.display_name, entry.size_str, entry.modified, key=str(i))

        self._set_status(f"{len(entries)} item(s)  —  {label}")

    # -------------------------------------------------------------------------
    # Focus / pane switching
    # -------------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Track which pane is active based on where the cursor moves."""
        new_active = "local" if event.data_table.id == "fm_local_table" else "remote"
        if new_active != self._active:
            self._active = new_active
            self._update_pane_styles()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter key: navigate into directories."""
        if event.data_table.id == "fm_local_table":
            self._active = "local"
            entry = self._local_selected()
            if entry and entry.is_dir:
                new_path = self._local_path / entry.name
                if new_path.is_dir():
                    self._local_path = new_path
                    self._refresh_local()
        else:
            self._active = "remote"
            entry = self._remote_selected()
            if entry and entry.is_dir:
                asyncio.ensure_future(self._list_remote(self._join_remote(entry.name)))

    # -------------------------------------------------------------------------
    # Button handler
    # -------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping: dict[str, Any] = {
            "fm_up_btn": self.action_go_up,
            "fm_newfolder_btn": self.action_new_folder,
            "fm_rename_btn": self.action_rename,
            "fm_copy_btn": self.action_copy,
            "fm_cut_btn": self.action_cut,
            "fm_paste_btn": self.action_paste,
            "fm_delete_btn": self.action_delete,
            "fm_transfer_btn": self.action_transfer,
            "fm_refresh_btn": self.action_refresh,
        }
        action = mapping.get(event.button.id)
        if action:
            result = action()
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)

    # -------------------------------------------------------------------------
    # Actions — navigation
    # -------------------------------------------------------------------------

    async def action_back(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.app.pop_screen()

    def action_switch_pane(self) -> None:
        self._active = "remote" if self._active == "local" else "local"
        self._update_pane_styles()
        tbl_id = "fm_remote_table" if self._active == "remote" else "fm_local_table"
        self.query_one(f"#{tbl_id}", DataTable).focus()

    def action_go_up(self) -> None:
        if self._active == "local":
            parent = self._local_path.parent
            if parent != self._local_path:
                self._local_path = parent
                self._refresh_local()
        else:
            if self._remote_path != "/":
                asyncio.ensure_future(self._list_remote(self._remote_parent()))

    def action_refresh(self) -> None:
        if self._active == "local":
            self._refresh_local()
        else:
            asyncio.ensure_future(self._list_remote(self._remote_path))

    def action_quit(self) -> None:
        self.app.exit()

    # -------------------------------------------------------------------------
    # Actions — file operations
    # -------------------------------------------------------------------------

    def action_new_folder(self) -> None:
        self.app.push_screen(NewFolderModal(), callback=self._do_new_folder)

    def _do_new_folder(self, name: str | None) -> None:
        if not name:
            return
        asyncio.ensure_future(self._new_folder_worker(name))

    async def _new_folder_worker(self, name: str) -> None:
        if self._active == "local":
            new_dir = self._local_path / name
            try:
                new_dir.mkdir(parents=False)
                self._refresh_local()
            except OSError as e:
                self._set_status(f"Create folder failed: {e}")
        else:
            if not self._client:
                self._set_status("Not connected.")
                return
            new_path = self._join_remote(name)
            self._set_status(f"Creating folder {name}…")
            try:
                await self._client.make_directory(new_path)
                await self._list_remote(self._remote_path)
            except FtpTransferError as e:
                self._set_status(f"Create folder failed: {e}")

    def action_rename(self) -> None:
        entry = self._active_selected()
        if entry is None:
            self._set_status("Select an item to rename.")
            return
        self.app.push_screen(
            RenameModal(entry.name),
            callback=lambda new_name: self._do_rename(entry, new_name),
        )

    def _do_rename(self, entry: FileEntry, new_name: str | None) -> None:
        if not new_name or new_name == entry.name:
            return
        asyncio.ensure_future(self._rename_worker(entry, new_name))

    async def _rename_worker(self, entry: FileEntry, new_name: str) -> None:
        if self._active == "local":
            old = self._local_path / entry.name
            new = self._local_path / new_name
            try:
                old.rename(new)
                self._refresh_local()
            except OSError as e:
                self._set_status(f"Rename failed: {e}")
        else:
            if not self._client:
                self._set_status("Not connected.")
                return
            self._set_status(f"Renaming {entry.name} → {new_name}…")
            try:
                await self._client.rename(
                    self._join_remote(entry.name),
                    self._join_remote(new_name),
                )
                await self._list_remote(self._remote_path)
            except FtpTransferError as e:
                self._set_status(f"Rename failed: {e}")

    def action_delete(self) -> None:
        entry = self._active_selected()
        if entry is None:
            self._set_status("Select an item to delete.")
            return
        self.app.push_screen(
            ConfirmDeleteModal(entry),
            callback=lambda confirmed: self._do_delete(entry, confirmed),
        )

    def _do_delete(self, entry: FileEntry, confirmed: bool) -> None:
        if not confirmed:
            return
        asyncio.ensure_future(self._delete_worker(entry))

    async def _delete_worker(self, entry: FileEntry) -> None:
        if self._active == "local":
            target = self._local_path / entry.name
            try:
                if entry.is_dir:
                    shutil.rmtree(target)
                else:
                    target.unlink()
                self._refresh_local()
            except OSError as e:
                self._set_status(f"Delete failed: {e}")
        else:
            if not self._client:
                self._set_status("Not connected.")
                return
            self._set_status(f"Deleting {entry.name}…")
            try:
                if entry.is_dir:
                    await self._client.delete_recursive(self._join_remote(entry.name))
                else:
                    await self._client.delete_file(self._join_remote(entry.name))
                await self._list_remote(self._remote_path)
            except FtpTransferError as e:
                self._set_status(f"Delete failed: {e}")

    # -------------------------------------------------------------------------
    # Copy / Cut / Paste
    # -------------------------------------------------------------------------

    def action_copy(self) -> None:
        entry = self._active_selected()
        if entry is None:
            self._set_status("Select an item to copy.")
            return
        self._clipboard_entry = entry
        self._clipboard_pane = self._active
        self._clipboard_src_path = (
            self._local_path / entry.name if self._active == "local"
            else self._join_remote(entry.name)
        )
        self._clipboard_op = "copy"
        self._set_status(f"[COPY] {entry.name}  —  navigate to destination pane/folder and press V to paste.")

    def action_cut(self) -> None:
        entry = self._active_selected()
        if entry is None:
            self._set_status("Select an item to cut.")
            return
        self._clipboard_entry = entry
        self._clipboard_pane = self._active
        self._clipboard_src_path = (
            self._local_path / entry.name if self._active == "local"
            else self._join_remote(entry.name)
        )
        self._clipboard_op = "cut"
        self._set_status(f"[CUT]  {entry.name}  —  navigate to destination pane/folder and press V to paste.")

    def action_paste(self) -> None:
        if self._clipboard_entry is None:
            self._set_status("Clipboard is empty — use C to copy or X to cut first.")
            return
        asyncio.ensure_future(self._paste_worker())

    async def _paste_worker(self) -> None:  # noqa: C901
        entry = self._clipboard_entry
        src_pane = self._clipboard_pane
        op = self._clipboard_op
        is_cut = op == "cut"

        if entry is None:
            return

        # ---- local → local ------------------------------------------------
        if src_pane == "local" and self._active == "local":
            src = Path(self._clipboard_src_path)  # type: ignore[arg-type]
            dst = self._local_path / entry.name
            if src == dst:
                self._set_status("Source and destination are the same folder — nothing to do.")
                return
            verb = "Moving" if is_cut else "Copying"
            self._set_status(f"{verb} {entry.name}…")
            try:
                if is_cut:
                    shutil.move(str(src), str(dst))
                    self._clipboard_entry = None
                    self._clipboard_src_path = None
                else:
                    if entry.is_dir:
                        shutil.copytree(str(src), str(dst))
                    else:
                        shutil.copy2(str(src), str(dst))
                self._refresh_local()
                self._set_status(f"{'Moved' if is_cut else 'Copied'} {entry.name}.")
            except OSError as e:
                self._set_status(f"{'Move' if is_cut else 'Copy'} failed: {e}")
            return

        # ---- remote → remote ----------------------------------------------
        # Aurora's FTP server does not support RNFR/RNTO for cross-directory
        # moves (returns 250 instead of the expected 350 to RNFR).  We work
        # around this by downloading to a temp dir, uploading to the new
        # location, then — for a cut — deleting the source using the same
        # recursive tree-walk already used by the Delete action.
        if src_pane == "remote" and self._active == "remote":
            if not self._client:
                self._set_status("Not connected to console.")
                return
            src_ftp = str(self._clipboard_src_path)
            dst_ftp = self._remote_path.rstrip("/") + "/" + entry.name
            if src_ftp.rstrip("/") == dst_ftp.rstrip("/"):
                self._set_status("Source and destination are the same — nothing to do.")
                return
            verb = "Moving" if is_cut else "Copying"
            modal = ProgressModal(title=f"{verb}: {entry.name}")
            await self.app.push_screen(modal)
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir) / entry.name
                    if entry.is_dir:
                        def _dl_dir(done: int, total: int, rel: str) -> None:
                            lbl = f"[{done + 1}/{total}] {rel}" if total else rel
                            modal.set_stage(f"Downloading {lbl}", done, total or 1)
                        await self._client.download_directory(src_ftp, tmp_path, progress_callback=_dl_dir)
                        def _ul_dir(done: int, total: int, rel: str) -> None:
                            lbl = f"[{done + 1}/{total}] {rel}" if total else rel
                            modal.set_stage(f"Uploading {lbl}", done, total or 1)
                        await self._client.upload_directory(tmp_path, dst_ftp, progress_callback=_ul_dir)
                    else:
                        def _dl_file(recv: int, total: int) -> None:
                            modal.set_stage(f"Downloading {entry.name}…", recv, total or entry.size)
                        await self._client.download_file(src_ftp, tmp_path, total_size=entry.size, progress_callback=_dl_file)
                        def _ul_file(sent: int, total: int) -> None:
                            modal.set_stage(f"Uploading {entry.name}…", sent, total or entry.size)
                        await self._client.upload_file(tmp_path, dst_ftp, progress_callback=_ul_file)
                if is_cut:
                    # Delete source using the recursive tree-walk (Aurora FTP
                    # requires individual DELE per file before RMD on a dir).
                    modal.set_stage(f"Removing source {entry.name}…", 0, 1)
                    if entry.is_dir:
                        await self._client.delete_recursive(src_ftp)
                    else:
                        await self._client.delete_file(src_ftp)
                    self._clipboard_entry = None
                    self._clipboard_src_path = None
                modal.set_done(f"{'Moved' if is_cut else 'Copied'} {entry.name}.", success=True)
                await self._list_remote(self._remote_path)
            except (FtpTransferError, FtpConnectionError) as e:
                modal.set_done(f"{'Move' if is_cut else 'Copy'} failed: {e}", success=False)
            return

        # ---- local → remote -----------------------------------------------
        if src_pane == "local" and self._active == "remote":
            if not self._client:
                self._set_status("Not connected to console.")
                return
            local_src = Path(self._clipboard_src_path)  # type: ignore[arg-type]
            remote_dst = self._remote_path.rstrip("/") + "/" + entry.name
            verb = "Moving" if is_cut else "Copying"
            modal = ProgressModal(title=f"{verb}: {entry.name}")
            await self.app.push_screen(modal)
            try:
                if entry.is_dir:
                    def _ul_dir2(done: int, total: int, rel: str) -> None:
                        lbl = f"[{done + 1}/{total}] {rel}" if total else rel
                        modal.set_stage(f"Uploading {lbl}", done, total or 1)
                    await self._client.upload_directory(local_src, remote_dst, progress_callback=_ul_dir2)
                else:
                    def _ul_file2(sent: int, total: int) -> None:
                        modal.set_stage(f"Uploading {entry.name}…", sent, total or entry.size)
                    await self._client.upload_file(local_src, remote_dst, progress_callback=_ul_file2)
                if is_cut:
                    if entry.is_dir:
                        shutil.rmtree(str(local_src))
                    else:
                        local_src.unlink()
                    self._clipboard_entry = None
                    self._clipboard_src_path = None
                    self._refresh_local()
                modal.set_done(f"{'Moved' if is_cut else 'Copied'} {entry.name}.", success=True)
                await self._list_remote(self._remote_path)
            except (FtpTransferError, FtpConnectionError, OSError) as e:
                modal.set_done(f"{'Move' if is_cut else 'Copy'} failed: {e}", success=False)
            return

        # ---- remote → local -----------------------------------------------
        if not self._client:
            self._set_status("Not connected to console.")
            return
        remote_src = str(self._clipboard_src_path)
        local_dst = self._local_path / entry.name
        verb = "Moving" if is_cut else "Copying"
        modal = ProgressModal(title=f"{verb}: {entry.name}")
        await self.app.push_screen(modal)
        try:
            if entry.is_dir:
                def _dl_dir2(done: int, total: int, rel: str) -> None:
                    lbl = f"[{done + 1}/{total}] {rel}" if total else rel
                    modal.set_stage(f"Downloading {lbl}", done, total or 1)
                await self._client.download_directory(remote_src, local_dst, progress_callback=_dl_dir2)
            else:
                def _dl_file2(recv: int, total: int) -> None:
                    modal.set_stage(f"Downloading {entry.name}…", recv, total or entry.size)
                await self._client.download_file(remote_src, local_dst, total_size=entry.size, progress_callback=_dl_file2)
            if is_cut:
                if entry.is_dir:
                    await self._client.delete_recursive(remote_src)
                else:
                    await self._client.delete_file(remote_src)
                self._clipboard_entry = None
                self._clipboard_src_path = None
                await self._list_remote(self._remote_path)
            modal.set_done(f"{'Moved' if is_cut else 'Copied'} {entry.name}.", success=True)
            self._refresh_local()
        except (FtpTransferError, FtpConnectionError) as e:
            modal.set_done(f"{'Move' if is_cut else 'Copy'} failed: {e}", success=False)

    # -------------------------------------------------------------------------
    # Transfer action (copy between panes)
    # -------------------------------------------------------------------------

    def action_transfer(self) -> None:
        entry = self._active_selected()
        if entry is None:
            self._set_status("Select a file or folder to transfer.")
            return
        if not self._client:
            self._set_status("Not connected to console — cannot transfer.")
            return
        asyncio.ensure_future(self._transfer_worker(entry))

    async def _transfer_worker(self, entry: FileEntry) -> None:
        if self._active == "local":
            local_src = self._local_path / entry.name
            remote_dest = self._join_remote(entry.name)
            if entry.is_dir:
                # Recursive upload: local folder → console
                modal = ProgressModal(title=f"Uploading folder: {entry.name}")
                await self.app.push_screen(modal)
                try:
                    def _udir_cb(done: int, total: int, rel: str) -> None:
                        label = f"[{done + 1}/{total}] {rel}" if total else rel
                        modal.set_stage(label, done, total or 1)

                    await self._client.upload_directory(  # type: ignore[attr-defined]
                        local_src, remote_dest, progress_callback=_udir_cb
                    )
                    modal.set_done(f"Uploaded {entry.name}/ to {remote_dest}", success=True)
                    await self._list_remote(self._remote_path)
                except (FtpTransferError, FtpConnectionError) as e:
                    modal.set_done(f"Upload failed: {e}", success=False)
            else:
                # Single file upload
                modal = ProgressModal(title=f"Uploading: {entry.name}")
                await self.app.push_screen(modal)
                try:
                    def _up_cb(sent: int, total: int) -> None:
                        modal.set_stage(f"Uploading {entry.name}…", sent, total or entry.size)

                    await self._client.upload_file(local_src, remote_dest, progress_callback=_up_cb)  # type: ignore[arg-type]
                    modal.set_done(f"Uploaded to {remote_dest}", success=True)
                    await self._list_remote(self._remote_path)
                except (FtpTransferError, FtpConnectionError) as e:
                    modal.set_done(f"Upload failed: {e}", success=False)
        else:
            remote_src = self._join_remote(entry.name)
            local_dest = self._local_path / entry.name
            if entry.is_dir:
                # Recursive download: console folder → local
                modal = ProgressModal(title=f"Downloading folder: {entry.name}")
                await self.app.push_screen(modal)
                try:
                    def _ddir_cb(done: int, total: int, rel: str) -> None:
                        label = f"[{done + 1}/{total}] {rel}" if total else rel
                        modal.set_stage(label, done, total or 1)

                    await self._client.download_directory(  # type: ignore[attr-defined]
                        remote_src, local_dest, progress_callback=_ddir_cb
                    )
                    modal.set_done(f"Saved {entry.name}/ to {local_dest}", success=True)
                    self._refresh_local()
                except (FtpTransferError, FtpConnectionError) as e:
                    modal.set_done(f"Download failed: {e}", success=False)
            else:
                # Single file download
                modal = ProgressModal(title=f"Downloading: {entry.name}")
                await self.app.push_screen(modal)
                try:
                    def _dl_cb(received: int, total: int) -> None:
                        modal.set_stage(f"Downloading {entry.name}…", received, total or entry.size)

                    await self._client.download_file(
                        remote_src, local_dest, total_size=entry.size, progress_callback=_dl_cb
                    )
                    modal.set_done(f"Saved to {local_dest}", success=True)
                    self._refresh_local()
                except (FtpTransferError, FtpConnectionError) as e:
                    modal.set_done(f"Download failed: {e}", success=False)
