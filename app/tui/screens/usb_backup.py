"""USB Backup & Restore TUI screens.

Entry point: UsbBackupScreen (hub).
Sub-screens: CreateBackupScreen, RestoreBackupScreen.

All screens are Linux-only. On non-Linux platforms a clear "not supported"
panel is shown and no operations are permitted.
"""
from __future__ import annotations

import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Label, ProgressBar, RichLog, Static

from app.core.usb_backup import (
    BADUPDATE_LABEL,
    REQUIRED_TOOLS,
    BackupMeta,
    BlockDevice,
    RestoreMode,
    all_dependencies_present,
    backup_image_path,
    check_dependencies,
    check_platform,
    check_restore_compat,
    create_backup,
    detect_removable_devices,
    get_backup_dir,
    list_backups,
    restore_backup,
)
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar


# ---------------------------------------------------------------------------
# CSS (scoped to this module's screens)
# ---------------------------------------------------------------------------

_CSS = """
#backup_hub { align: center middle; width: 100%; height: 100%; }
#backup_hub_box { width: 60; height: auto; padding: 1 2; border: thick $primary; background: $surface; }
#backup_hub_box Button { width: 100%; margin-top: 1; }
#unsupported_box { width: 70; height: auto; padding: 1 2; border: thick $error; background: $surface; }
#dep_box { width: 70; height: auto; padding: 1 2; border: thick $warning; background: $surface; }
#dep_box Button { width: 100%; margin-top: 1; }

#device_table { height: 12; }
#backup_table  { height: 12; }
#op_log { height: 12; border: solid $primary; }
#progress_bar  { margin: 1 0; }
#compat_box { padding: 1 2; border: solid $warning; background: $surface; margin: 1 0; }
#confirm_modal_box { width: 70; height: auto; padding: 1 2; border: thick $error; background: $surface; }
#confirm_modal_box Button { width: 100%; margin-top: 1; }
"""


# ---------------------------------------------------------------------------
# Shared confirm modal
# ---------------------------------------------------------------------------

class ConfirmDestructiveModal(ModalScreen[bool]):
    """Generic destructive-action confirmation modal."""

    DEFAULT_CSS = """
    ConfirmDestructiveModal { align: center middle; }
    #confirm_modal_box { width: 70; height: auto; padding: 1 2;
                         border: thick $error; background: $surface; }
    #confirm_modal_box Button { width: 100%; margin-top: 1; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_modal_box"):
            yield Static(f"[b red]{self._title}[/]")
            yield Static(self._body)
            yield Button("Proceed — I understand this is irreversible", id="c_yes", variant="error")
            yield Button("Cancel", id="c_no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "c_yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Progress panel (inline widget, not exported)
# ---------------------------------------------------------------------------

class _ProgressPanel(Vertical):
    """Live progress display: stage label + bar + log + elapsed time."""

    DEFAULT_CSS = """
    _ProgressPanel { height: auto; }
    #pp_stage { margin-bottom: 1; }
    #pp_bar   { margin-bottom: 1; }
    #pp_log   { height: 10; border: solid $primary; }
    #pp_elapsed { color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        yield Static("Initialising…", id="pp_stage")
        yield ProgressBar(total=100, show_eta=False, id="pp_bar")
        yield RichLog(id="pp_log", highlight=False, markup=True, wrap=True)
        yield Static("Elapsed: 0s", id="pp_elapsed")

    def on_mount(self) -> None:
        self._start = time.monotonic()
        self._timer = self.set_interval(1.0, self._tick_elapsed)

    def _tick_elapsed(self) -> None:
        elapsed = int(time.monotonic() - self._start)
        try:
            self.query_one("#pp_elapsed", Static).update(f"Elapsed: {elapsed}s")
        except Exception:
            pass

    def update_progress(self, pct: float, stage: str) -> None:
        try:
            self.query_one("#pp_stage", Static).update(stage)
            self.query_one("#pp_bar", ProgressBar).update(progress=pct)
            if stage:
                self.query_one("#pp_log", RichLog).write(stage)
        except Exception:
            pass

    def stop_timer(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hub screen
# ---------------------------------------------------------------------------

class UsbBackupScreen(Screen):
    """Entry point. Checks OS + dependencies before showing actions."""

    TITLE = "USB Backup / Restore"
    DEFAULT_CSS = _CSS
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Vertical(id="backup_hub"):
            yield Static("", id="hub_content")  # replaced in on_mount
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

        hub = self.query_one("#hub_content", Static)
        hub.remove()  # remove placeholder, compose real content below
        container = self.query_one("#backup_hub", Vertical)

        if not check_platform():
            container.mount(self._not_supported_widget())
            return

        deps = check_dependencies()
        if not all(deps.values()):
            container.mount(self._missing_deps_widget(deps))
            return

        container.mount(self._hub_widget())
        self.query_one("#status_bar", StatusBar).set_text(
            "Select an operation. Backup requires sudo privileges."
        )

    def _not_supported_widget(self) -> Vertical:
        return Vertical(
            Static("[b red]USB Backup / Restore — Linux Only[/]"),
            Static(
                "\nThis feature uses Linux block-level tools (partclone, parted) "
                "that are not available on Windows.\n\n"
                "To use this feature, run x360tm on a Linux machine."
            ),
            Button("Back", id="hub_back", variant="primary"),
            id="unsupported_box",
        )

    def _missing_deps_widget(self, deps: dict[str, bool]) -> Vertical:
        missing = [t for t, ok in deps.items() if not ok]
        return Vertical(
            Static("[b yellow]Missing Required Tools[/]"),
            Static("\nThe following tools must be installed before using USB Backup / Restore:"),
            *[Static(f"  [red]✗[/]  {tool}") for tool in missing],
            Static(
                "\nInstall command:\n"
                "[bold]  sudo apt install partclone zstd parted fatresize[/bold]"
            ),
            Button("Install missing tools", id="hub_install", variant="warning"),
            Button("Re-check", id="hub_recheck", variant="primary"),
            Button("Back", id="hub_back"),
            id="dep_box",
        )

    def _hub_widget(self) -> Vertical:
        return Vertical(
            Static("[b cyan]USB Backup / Restore[/]"),
            Static(
                "\nCreate or restore a full block-level image of your Xbox 360 exploit USB.\n"
                "[dim]Requires elevated privileges (sudo) for block-level access.[/dim]"
            ),
            Button("Create Backup", id="hub_backup", variant="success"),
            Button("Restore from Backup", id="hub_restore", variant="warning"),
            Button("Back [Esc]", id="hub_back"),
            id="backup_hub_box",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "hub_back":
            self.app.pop_screen()
        elif bid == "hub_backup":
            self.app.push_screen(CreateBackupScreen())
        elif bid == "hub_restore":
            self.app.push_screen(RestoreBackupScreen())
        elif bid == "hub_install":
            self.app.push_screen(_InstallDepsScreen())
        elif bid == "hub_recheck":
            # Remount the hub by popping and re-pushing
            self.app.pop_screen()
            self.app.push_screen(UsbBackupScreen())

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Dependency installer screen
# ---------------------------------------------------------------------------

class _InstallDepsScreen(Screen):
    """Shows the install command — user must run it in a terminal."""

    TITLE = "Install Dependencies"
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            yield Static("[b]Install Required Tools[/b]")
            yield Static(
                "\nRun this command in a terminal, then come back and press [b]Re-check[/b]:"
            )
            yield Static(
                "\n[bold]  sudo apt install partclone zstd parted fatresize[/bold]\n"
            )
            yield Static(
                "[dim]Note: on some systems partclone.fat may live in /usr/sbin — "
                "if the re-check still shows it missing, try:\n"
                "  ls /usr/sbin/partclone.fat[/dim]"
            )
            with Horizontal():
                yield Button("Re-check", id="inst_recheck", variant="primary")
                yield Button("Back [Esc]", id="inst_back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "inst_back":
            self.app.pop_screen()
        elif event.button.id == "inst_recheck":
            # Pop install screen, pop hub, push fresh hub so dep check reruns
            self.app.pop_screen()
            self.app.pop_screen()
            self.app.push_screen(UsbBackupScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Create Backup screen
# ---------------------------------------------------------------------------

class CreateBackupScreen(Screen):
    """Device selection → confirm → backup progress → result."""

    TITLE = "Create USB Backup"
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[BlockDevice] = []
        self._selected: BlockDevice | None = None
        self._in_progress = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="cb_scroll"):
            yield Static("[b cyan]Create USB Backup[/]")
            yield Static("[dim]Select the USB device to back up.[/]")
            yield Static(
                "[yellow]⚠ The backup operation requires sudo. When you press "
                "Back Up Selected Device, a sudo password prompt will appear "
                "in the terminal where you launched x360tm — switch to it and "
                "enter your password if prompted.[/]",
                id="cb_sudo_warn",
            )
            yield DataTable(id="device_table", cursor_type="row")
            with Horizontal():
                yield Button("Refresh", id="cb_refresh", variant="default")
                yield Button("Back Up Selected Device", id="cb_start", variant="success", disabled=True)
                yield Button("Back [Esc]", id="cb_back")
            yield Static("", id="cb_status", classes="muted")
            yield _ProgressPanel(id="cb_progress")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#cb_progress", _ProgressPanel).display = False
        table = self.query_one("#device_table", DataTable)
        table.add_columns("Device", "Partition", "Label", "Filesystem", "Size", "Mount", "Note")
        self._load_devices()

    def _load_devices(self) -> None:
        self._devices = detect_removable_devices()
        table = self.query_one("#device_table", DataTable)
        table.clear()
        self._selected = None
        self.query_one("#cb_start", Button).disabled = True

        if not self._devices:
            self.query_one("#cb_status", Static).update(
                "[yellow]No removable devices detected. Insert your USB stick and press Refresh.[/]"
            )
            return

        for dev in self._devices:
            size_gib = dev.total_bytes / (1024 ** 3)
            note = "[green]★ auto-detected[/]" if dev.is_suggested else ""
            table.add_row(
                dev.device,
                dev.partition,
                dev.label or "(no label)",
                dev.filesystem or "?",
                f"{size_gib:.1f} GiB",
                dev.mountpoint or "—",
                note,
            )

        # Pre-select suggested device
        suggested_idx = next((i for i, d in enumerate(self._devices) if d.is_suggested), 0)
        table.move_cursor(row=suggested_idx)
        self._selected = self._devices[suggested_idx]
        self.query_one("#cb_start", Button).disabled = False
        self.query_one("#cb_status", Static).update(
            f"[green]{len(self._devices)} device(s) found.[/]"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._devices):
            self._selected = self._devices[idx]
            self.query_one("#cb_start", Button).disabled = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._devices):
            self._selected = self._devices[idx]
            self.query_one("#cb_start", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cb_back":
            self.app.pop_screen()
        elif bid == "cb_refresh":
            self._load_devices()
        elif bid == "cb_start" and self._selected and not self._in_progress:
            self.run_worker(self._confirm_and_backup(), exclusive=True)

    async def _confirm_and_backup(self) -> None:
        dev = self._selected
        if not dev:
            return
        size_gib = dev.total_bytes / (1024 ** 3)
        confirmed = await self.app.push_screen_wait(
            ConfirmDestructiveModal(
                "Confirm Backup",
                f"Back up [bold]{dev.partition}[/bold] ({dev.label or 'no label'}, "
                f"{size_gib:.1f} GiB)?\n\n"
                "This will read the entire partition and may take several minutes.\n"
                "The USB stick will remain intact — this is a read-only operation.",
            )
        )
        if not confirmed:
            return

        self._in_progress = True
        self.query_one("#cb_start", Button).disabled = True
        self.query_one("#cb_refresh", Button).disabled = True
        self.query_one("#cb_back", Button).disabled = True
        panel = self.query_one("#cb_progress", _ProgressPanel)
        panel.display = True

        backup_dir = get_backup_dir(self.app.settings)  # type: ignore[attr-defined]
        status = self.query_one("#cb_status", Static)

        def _progress(pct: float, msg: str) -> None:
            panel.update_progress(pct, msg)

        try:
            meta = await create_backup(dev, backup_dir, _progress)
            panel.stop_timer()
            panel.update_progress(100.0, "[green]✓ Backup complete![/]")
            img_size = (backup_dir / meta.image_file).stat().st_size / (1024 ** 3)
            status.update(
                f"[green]Saved:[/] {meta.image_file}  "
                f"({img_size:.2f} GiB compressed, {meta.used_gib:.2f} GiB used data)"
            )
        except Exception as e:
            panel.stop_timer()
            panel.update_progress(0.0, f"[red]✗ Backup failed: {e}[/]")
            status.update(f"[red]Error: {e}[/]")
        finally:
            self._in_progress = False
            self.query_one("#cb_refresh", Button).disabled = False
            self.query_one("#cb_back", Button).disabled = False

    def action_back(self) -> None:
        if not self._in_progress:
            self.app.pop_screen()


# ---------------------------------------------------------------------------
# Restore screen
# ---------------------------------------------------------------------------

class RestoreBackupScreen(Screen):
    """Backup list → target device selection → size check → confirm → restore."""

    TITLE = "Restore USB Backup"
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._backups: list[BackupMeta] = []
        self._devices: list[BlockDevice] = []
        self._selected_backup: BackupMeta | None = None
        self._selected_device: BlockDevice | None = None
        self._in_progress = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="rb_scroll"):
            yield Static("[b cyan]Restore USB Backup[/]")
            yield Static("[dim]Step 1: Select a backup to restore.[/]")
            yield DataTable(id="backup_table", cursor_type="row")
            yield Static("[dim]Step 2: Select target USB device.[/]")
            yield Static(
                "[yellow]⚠ ALL DATA on the target device will be ERASED.[/]",
                id="rb_warn",
            )
            yield DataTable(id="device_table", cursor_type="row")
            yield Static("", id="rb_compat", classes="muted")
            with Horizontal():
                yield Button("Refresh Devices", id="rb_refresh", variant="default")
                yield Button("Restore", id="rb_start", variant="error", disabled=True)
                yield Button("Back [Esc]", id="rb_back")
            yield Static("", id="rb_status", classes="muted")
            yield _ProgressPanel(id="rb_progress")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#rb_progress", _ProgressPanel).display = False

        # Backup table
        bt = self.query_one("#backup_table", DataTable)
        bt.add_columns("Date", "Label", "Source", "Total", "Used", "Image")

        # Device table
        dt = self.query_one("#device_table", DataTable)
        dt.add_columns("Device", "Partition", "Label", "FS", "Size", "Mount", "Note")

        self._load_backups()
        self._load_devices()

    def _load_backups(self) -> None:
        backup_dir = get_backup_dir(self.app.settings)  # type: ignore[attr-defined]
        self._backups = list_backups(backup_dir)
        bt = self.query_one("#backup_table", DataTable)
        bt.clear()
        self._selected_backup = None

        if not self._backups:
            self.query_one("#rb_status", Static).update(
                "[yellow]No backups found. Create a backup first.[/]"
            )
            return

        for m in self._backups:
            total_gib = m.source_disk_total_bytes / (1024 ** 3)
            bt.add_row(
                m.timestamp_display,
                m.volume_label,
                m.source_partition,
                f"{total_gib:.1f} GiB",
                f"{m.used_gib:.2f} GiB",
                m.image_file,
            )

        self._selected_backup = self._backups[0]
        bt.move_cursor(row=0)
        self._update_compat()

    def _load_devices(self) -> None:
        self._devices = detect_removable_devices()
        dt = self.query_one("#device_table", DataTable)
        dt.clear()
        self._selected_device = None
        self.query_one("#rb_start", Button).disabled = True

        for dev in self._devices:
            size_gib = dev.total_bytes / (1024 ** 3)
            note = "[green]★ auto-detected[/]" if dev.is_suggested else ""
            dt.add_row(
                dev.device,
                dev.partition,
                dev.label or "(no label)",
                dev.filesystem or "?",
                f"{size_gib:.1f} GiB",
                dev.mountpoint or "—",
                note,
            )

        if self._devices:
            suggested_idx = next((i for i, d in enumerate(self._devices) if d.is_suggested), 0)
            dt.move_cursor(row=suggested_idx)
            self._selected_device = self._devices[suggested_idx]
            self._update_compat()

    def _update_compat(self) -> None:
        compat = self.query_one("#rb_compat", Static)
        self.query_one("#rb_start", Button).disabled = True

        if not self._selected_backup or not self._selected_device:
            compat.update("")
            return

        meta = self._selected_backup
        dev = self._selected_device
        mode = check_restore_compat(meta, dev.partition_bytes)
        target_gib = dev.partition_bytes / (1024 ** 3)
        source_gib = meta.source_disk_total_bytes / (1024 ** 3)

        if mode == RestoreMode.TOO_SMALL:
            needed = meta.used_bytes * 1.05 / (1024 ** 3)
            compat.update(
                f"[red]✗ Target too small.[/]  "
                f"Backup uses {meta.used_gib:.2f} GiB — target has {target_gib:.1f} GiB.\n"
                f"Need at least {needed:.2f} GiB. Cannot proceed."
            )
        elif mode == RestoreMode.SHRINK:
            compat.update(
                f"[green]✓ Target is large enough ({target_gib:.1f} GiB ≥ {meta.used_gib:.2f} GiB used).[/]\n"
                f"Target is smaller than source ({source_gib:.1f} GiB) — partition will be resized to fit.\n"
                f"[yellow]⚠ ALL data on {dev.device} will be erased.[/]"
            )
            self.query_one("#rb_start", Button).disabled = False
        else:  # EXACT
            compat.update(
                f"[green]✓ Target ({target_gib:.1f} GiB) ≥ source ({source_gib:.1f} GiB). Exact restore.[/]\n"
                f"[yellow]⚠ ALL data on {dev.device} will be erased.[/]"
            )
            self.query_one("#rb_start", Button).disabled = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table_id = event.data_table.id
        idx = event.cursor_row
        if table_id == "backup_table":
            if 0 <= idx < len(self._backups):
                self._selected_backup = self._backups[idx]
                self._update_compat()
        elif table_id == "device_table":
            if 0 <= idx < len(self._devices):
                self._selected_device = self._devices[idx]
                self._update_compat()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Same as highlight for confirm-on-click UX
        self.on_data_table_row_highlighted(event)  # type: ignore[arg-type]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "rb_back":
            self.app.pop_screen()
        elif bid == "rb_refresh":
            self._load_devices()
        elif bid == "rb_start" and not self._in_progress:
            self.run_worker(self._confirm_and_restore(), exclusive=True)

    async def _confirm_and_restore(self) -> None:
        meta = self._selected_backup
        dev = self._selected_device
        if not meta or not dev:
            return

        mode = check_restore_compat(meta, dev.partition_bytes)
        mode_label = {
            RestoreMode.EXACT: "exact restore (filesystem expanded if target is larger)",
            RestoreMode.SHRINK: "shrink restore (target repartitioned to fit used data)",
        }.get(mode, "unknown")

        target_gib = dev.total_bytes / (1024 ** 3)
        confirmed = await self.app.push_screen_wait(
            ConfirmDestructiveModal(
                "Confirm Restore — DATA WILL BE ERASED",
                f"Restore [bold]{meta.image_file}[/bold]\n"
                f"  Source: {meta.source_partition}  ({meta.total_gib:.1f} GiB, "
                f"{meta.used_gib:.2f} GiB used)\n"
                f"  Target: {dev.partition}  ({target_gib:.1f} GiB)\n"
                f"  Mode:   {mode_label}\n\n"
                f"[bold red]ALL DATA on {dev.device} ({dev.label or 'unlabelled'}) "
                f"will be permanently erased.[/bold red]",
            )
        )
        if not confirmed:
            return

        self._in_progress = True
        self.query_one("#rb_start", Button).disabled = True
        self.query_one("#rb_refresh", Button).disabled = True
        self.query_one("#rb_back", Button).disabled = True
        panel = self.query_one("#rb_progress", _ProgressPanel)
        panel.display = True

        backup_dir = get_backup_dir(self.app.settings)  # type: ignore[attr-defined]
        image_path = backup_image_path(backup_dir, meta)
        status = self.query_one("#rb_status", Static)

        if not image_path.exists():
            status.update(f"[red]Image file not found: {image_path}[/]")
            self._in_progress = False
            self.query_one("#rb_refresh", Button).disabled = False
            self.query_one("#rb_back", Button).disabled = False
            return

        def _progress(pct: float, msg: str) -> None:
            panel.update_progress(pct, msg)

        try:
            await restore_backup(meta, image_path, dev, _progress)
            panel.stop_timer()
            panel.update_progress(100.0, "[green]✓ Restore complete![/]")
            status.update(
                f"[green]USB restored successfully.[/] "
                f"Eject {dev.device} safely and insert into your Xbox 360."
            )
        except Exception as e:
            panel.stop_timer()
            panel.update_progress(0.0, f"[red]✗ Restore failed: {e}[/]")
            status.update(f"[red]Error: {e}[/]")
        finally:
            self._in_progress = False
            self.query_one("#rb_refresh", Button).disabled = False
            self.query_one("#rb_back", Button).disabled = False

    def action_back(self) -> None:
        if not self._in_progress:
            self.app.pop_screen()
