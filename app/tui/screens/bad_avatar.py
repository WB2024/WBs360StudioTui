"""Create BadAvatar USB — TUI screen.

Single-screen flow:
  1. Platform + source-file gate (error panel if either fails)
  2. Device selection table (detect_removable_devices, BADUPDATE auto-highlighted)
  3. Aurora auto-boot toggle (Checkbox, default ON)
  4. [Build BadAvatar USB] → sudo password modal → confirm format modal → live log
  5. Completion panel

Reuses SudoPasswordModal and ConfirmDestructiveModal from usb_backup to keep
the sudo credential UX consistent across the app.

See BADAVATAR_USB_SPEC.md §5 and §8 for design rationale.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, RichLog, Static

from app.core.bad_avatar import (
    AURORA_LAUNCH_PATH,
    check_platform,
    check_source_files,
    copy_files,
    format_and_mount,
    get_source_dir,
    patch_launch_ini,
    rename_aurora,
    sync_and_unmount,
    write_info_txt,
)
from app.core.usb_backup import BlockDevice, detect_removable_devices, sudo_authenticate
from app.tui.screens.usb_backup import ConfirmDestructiveModal, SudoPasswordModal
from app.tui.widgets.connection_bar import ConnectionBar

# ---------------------------------------------------------------------------
# ASCII art header (from ASCII.txt at repo root)
# ---------------------------------------------------------------------------

_LOGO = (
    " ░▒▓██████▓▒░       ░▒▓███████▓▒░  ░▒▓██████▓▒░ ░▒▓███████▓▒░        ░▒▓██████▓▒░       ░▒▓███████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓███████▓▒░ ░▒▓████████▓▒░░▒▓███████▓▒░  \n"
    "░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓████████▓▒░      ░▒▓███████▓▒░ ░▒▓████████▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓████████▓▒░      ░▒▓███████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓██████▓▒░  ░▒▓███████▓▒░  \n"
    "░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░      ░▒▓███████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░░▒▓███████▓▒░       ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓███████▓▒░  ░▒▓██████▓▒░ ░▒▓█▓▒░░▒▓████████▓▒░░▒▓███████▓▒░ ░▒▓████████▓▒░░▒▓█▓▒░░▒▓█▓▒░ \n"
    "\n"
    "\n"
    "░▒▓███████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓███████▓▒░ ░▒▓███████▓▒░ ░▒▓████████▓▒░░▒▓███████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ \n"
    "░▒▓███████▓▒░  ░▒▓██████▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓███████▓▒░  ░▒▓██████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓██████▓▒░ ░▒▓████████▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░   ░▒▓█▓▒░          ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░              ░▒▓█▓▒░ \n"
    "░▒▓█▓▒░░▒▓█▓▒░   ░▒▓█▓▒░          ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░       ░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░              ░▒▓█▓▒░ \n"
    "░▒▓███████▓▒░    ░▒▓█▓▒░           ░▒▓█████████████▓▒░ ░▒▓███████▓▒░ ░▒▓████████▓▒░░▒▓████████▓▒░░▒▓████████▓▒░       ░▒▓█▓▒░ \n"
)

# ---------------------------------------------------------------------------
# Screen CSS
# ---------------------------------------------------------------------------

_CSS = """
BadAvatarScreen { background: $background; }

#ba_logo {
    color: $accent;
    padding: 0 0 0 0;
    text-style: bold;
    overflow-x: auto;
}

#ba_desc { color: $text-muted; padding: 0 0 1 0; }

#ba_error_box {
    padding: 1 2;
    border: thick $error;
    background: $surface;
    margin: 1 0;
}

#ba_section_label { text-style: bold; margin-top: 1; }

#ba_device_table { height: 8; margin: 0 0 1 0; }

#ba_refresh_row { height: auto; margin-bottom: 1; }

#ba_sel_status { margin-bottom: 1; }

#ba_toggle_row { height: auto; margin: 1 0; }
#ba_toggle_hint { color: $text-muted; padding: 0 0 0 4; }

#ba_buttons { height: auto; margin: 1 0; }
#ba_buttons Button { margin-right: 1; }

#ba_log { height: 18; border: solid $primary; margin-top: 1; }

#ba_done_msg { margin-top: 1; text-style: bold; }
"""


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class BadAvatarScreen(Screen):
    """Create a BadAvatar USB from workspace source files.

    Full flow: platform check → source check → device select → sudo auth
    → confirm format → live progress log → completion.
    """

    TITLE = "Create BadAvatar USB"
    DEFAULT_CSS = _CSS
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[BlockDevice] = []
        self._selected: BlockDevice | None = None
        self._sudo_password: str | None = None
        self._in_progress = False
        self._done = False
        self._platform_ok = False
        self._source_ok = False

    # -----------------------------------------------------------------------
    # Compose
    # -----------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with VerticalScroll(id="ba_scroll"):
            yield Static(_LOGO, id="ba_logo")
            yield Static(
                "[dim]Avatar exploit USB — ABadAvatar v1.1 + XeUnshackle 1.03 + Aurora 0.7b.2  |  No internet required[/]",
                id="ba_desc",
            )
            # Error panel — shown when platform/source check fails
            yield Static("", id="ba_error_msg")

            # --- Device selection ---
            yield Static("[b]Select USB Device[/]", id="ba_section_label")
            yield DataTable(id="ba_device_table", cursor_type="row")
            with Horizontal(id="ba_refresh_row"):
                yield Button("Refresh Devices", id="ba_refresh")
            yield Static("", id="ba_sel_status", classes="muted")

            # --- Aurora toggle ---
            with Vertical(id="ba_toggle_row"):
                yield Checkbox(
                    "Set Aurora as default dashboard (recommended)",
                    id="ba_aurora_toggle",
                    value=True,
                )
                yield Static(
                    f"[dim]Checked → launch.ini Default = {AURORA_LAUNCH_PATH}[/]\n"
                    "[dim]Unchecked → Default left blank; configure DashLaunch manually on console[/]",
                    id="ba_toggle_hint",
                )

            # --- Action buttons ---
            with Horizontal(id="ba_buttons"):
                yield Button(
                    "Build BadAvatar USB",
                    id="ba_build",
                    variant="error",
                    disabled=True,
                )
                yield Button("Back [Esc]", id="ba_back")

            # --- Live progress log ---
            yield RichLog(id="ba_log", highlight=False, markup=True, wrap=True)

            # --- Completion message ---
            yield Static("", id="ba_done_msg")

        yield Footer()

    # -----------------------------------------------------------------------
    # Mount
    # -----------------------------------------------------------------------

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

        # Hide the progress log until the build starts
        self.query_one("#ba_log", RichLog).display = False

        # Gate on platform and source files
        self._platform_ok = check_platform()
        if not self._platform_ok:
            self._show_error(
                "[b red]Not Supported on This Platform[/]\n\n"
                "Creating a BadAvatar USB requires Linux block-level tools "
                "(mkfs.vfat, mount).\n"
                "Run x360tm on a Linux machine to use this feature."
            )
            return

        self._source_ok, msg = check_source_files()
        if not self._source_ok:
            self._show_error(f"[b red]Source Files Missing[/]\n\n{msg}")
            return

        # Checks passed — wire up the device table and load
        self._setup_device_table()
        self._load_devices()

    def _show_error(self, msg: str) -> None:
        """Display an error panel and hide all the device/build widgets."""
        self.query_one("#ba_error_msg", Static).update(msg)
        for wid_id in (
            "ba_section_label", "ba_device_table", "ba_refresh_row",
            "ba_sel_status", "ba_toggle_row", "ba_buttons",
        ):
            try:
                self.query_one(f"#{wid_id}").display = False
            except Exception:
                pass

    def _setup_device_table(self) -> None:
        table = self.query_one("#ba_device_table", DataTable)
        table.add_columns("Device", "Label", "Filesystem", "Size (total)", "Mount", "Note")

    def _load_devices(self) -> None:
        self._devices = detect_removable_devices()
        table = self.query_one("#ba_device_table", DataTable)
        table.clear()
        self._selected = None
        self.query_one("#ba_build", Button).disabled = True
        self.query_one("#ba_sel_status", Static).update("")

        if not self._devices:
            self.query_one("#ba_sel_status", Static).update(
                "[yellow]No removable USB drives detected. Insert your USB stick and press Refresh Devices.[/]"
            )
            return

        for dev in self._devices:
            size_gib = dev.total_bytes / (1024 ** 3)
            note = "[green]★ BADUPDATE[/]" if dev.is_suggested else ""
            table.add_row(
                dev.device,
                dev.label or "(none)",
                dev.filesystem or "—",
                f"{size_gib:.1f} GiB",
                dev.mountpoint or "—",
                note,
            )

        # Auto-select a suggested (BADUPDATE-labelled) device, or fall back to first
        suggested_idx = next(
            (i for i, d in enumerate(self._devices) if d.is_suggested), 0
        )
        table.move_cursor(row=suggested_idx)
        self._selected = self._devices[suggested_idx]
        self.query_one("#ba_build", Button).disabled = False

        note_text = " [green](★ BADUPDATE auto-selected)[/]" if self._devices[suggested_idx].is_suggested else ""
        self.query_one("#ba_sel_status", Static).update(
            f"[green]{len(self._devices)} device(s) found.[/]{note_text}"
        )

    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._devices):
            self._selected = self._devices[idx]
            self.query_one("#ba_build", Button).disabled = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._devices):
            self._selected = self._devices[idx]
            self.query_one("#ba_build", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "ba_back":
            self.action_back()
        elif bid == "ba_refresh":
            self._load_devices()
        elif bid == "ba_build" and not self._in_progress and not self._done:
            self.run_worker(self._build_flow(), exclusive=True)

    # -----------------------------------------------------------------------
    # Async build workflow
    # -----------------------------------------------------------------------

    async def _build_flow(self) -> None:
        """Full build workflow: sudo auth → confirm → format → copy → patch → done."""
        # Step 1: sudo authentication (collect password if not already cached)
        if self._sudo_password is None:
            ok = await self._sudo_preauth()
            if not ok:
                return

        # Step 2: confirm destructive format
        dev = self._selected
        if dev is None:
            return

        size_gib = dev.total_bytes / (1024 ** 3)
        confirmed = await self.app.push_screen_wait(
            ConfirmDestructiveModal(
                "⚠ Format USB — All Data Will Be Erased",
                f"\nDevice:   {dev.device}\n"
                f"Size:     {size_gib:.1f} GiB\n"
                f"Label:    {dev.label or '(none)'}\n\n"
                "This will [b]permanently erase ALL data[/] on the selected device.\n"
                "The drive will be formatted as FAT32 with label BADUPDATE.\n\n"
                "[yellow]Double-check you have selected the correct device.[/]",
            )
        )
        if not confirmed:
            return

        # Step 3: run the build
        await self._run_build()

    async def _sudo_preauth(self) -> bool:
        """Push SudoPasswordModal, authenticate, retry on wrong password.

        Stores the password in self._sudo_password on success.
        Returns True on success, False if the user cancelled.
        """
        status = self.query_one("#ba_sel_status", Static)
        while True:
            pw = await self.app.push_screen_wait(SudoPasswordModal())
            if pw is None:
                return False
            status.update("[yellow]Authenticating…[/]")
            ok = await sudo_authenticate(pw)
            if ok:
                self._sudo_password = pw
                status.update("[green]Sudo authenticated.[/]")
                return True
            status.update(
                "[red]Incorrect sudo password — please try again.[/]"
            )

    async def _run_build(self) -> None:
        """Execute the full 6-stage USB build, logging each step to #ba_log."""
        if not self._selected or not self._sudo_password:
            return

        self._in_progress = True
        self.query_one("#ba_build", Button).disabled = True
        self.query_one("#ba_refresh", Button).disabled = True

        log_widget = self.query_one("#ba_log", RichLog)
        log_widget.display = True

        device = self._selected.device
        aurora_default = self.query_one("#ba_aurora_toggle", Checkbox).value
        source = get_source_dir()

        # _log is called from the async event loop — direct widget call is safe
        def _log(msg: str) -> None:
            log_widget.write(msg)

        try:
            # ── Stage 1: Format + mount ──────────────────────────────────────
            _log("[b]Stage 1/6:[/] Formatting and mounting USB...")
            mount = await format_and_mount(device, self._sudo_password, progress_cb=_log)
            _log(f"[green]✓[/] Mounted at {mount}")

            # ── Stage 2: Copy files ──────────────────────────────────────────
            _log("\n[b]Stage 2/6:[/] Copying files (~251 MB — this may take a few minutes)...")

            last_pct: list[int] = [-1]

            def _copy_progress(filename: str, copied: int, total: int) -> None:
                # Called from thread executor — must use call_from_thread
                pct = int((copied / total) * 100) if total else 0
                if pct != last_pct[0] and pct % 10 == 0:
                    last_pct[0] = pct
                    self.app.call_from_thread(
                        log_widget.write,
                        f"    [{pct:3d}%]  {filename}",
                    )

            await copy_files(source, mount, progress_cb=_copy_progress)
            _log("[green]✓[/] All files copied")

            # ── Stage 3: Rename Aurora folder ────────────────────────────────
            _log("\n[b]Stage 3/6:[/] Renaming Aurora folder (Aurora 0.7b.2 → Aurora)...")
            rename_aurora(mount)
            _log("[green]✓[/] Aurora folder renamed")

            # ── Stage 4: Patch launch.ini ─────────────────────────────────────
            if aurora_default:
                _log(f"\n[b]Stage 4/6:[/] Patching launch.ini → Default = {AURORA_LAUNCH_PATH}...")
                patch_launch_ini(mount, set_aurora_default=True)
                _log("[green]✓[/] launch.ini patched — Aurora will auto-boot on power-on")
            else:
                _log("\n[b]Stage 4/6:[/] Skipping launch.ini patch (Aurora auto-boot not requested)")
                patch_launch_ini(mount, set_aurora_default=False)
                _log("[dim]✓  Default= left blank — configure DashLaunch on the console[/]")

            # ── Stage 5: Write info.txt ───────────────────────────────────────
            _log("\n[b]Stage 5/6:[/] Writing info.txt...")
            write_info_txt(mount)
            _log("[green]✓[/] info.txt written")

            # ── Stage 6: Sync + unmount ───────────────────────────────────────
            _log("\n[b]Stage 6/6:[/] Syncing and unmounting...")
            await sync_and_unmount(device, self._sudo_password, progress_cb=_log)

            # ── Done ─────────────────────────────────────────────────────────
            self._done = True
            _log("")
            _log("[b green]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]")
            _log("[b green]  ✓  BadAvatar USB created successfully![/]")
            _log("[b green]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]")
            _log("")
            _log("  Exploit:    ABadAvatar v1.1 + XeUnshackle 1.03")
            _log("  Dashboard:  Aurora 0.7b.2")
            if aurora_default:
                _log(f"  Auto-boot:  {AURORA_LAUNCH_PATH}")
            else:
                _log("  Auto-boot:  Not set — configure DashLaunch on console")
            _log("")
            _log("[dim]Plug the USB into Xbox port Usb0 (front-left), power on, and follow the XeUnshackle prompts.[/]")

            self.query_one("#ba_done_msg", Static).update(
                "[b green]✓ USB is ready. Safely eject the drive before unplugging.[/]"
            )

        except Exception as exc:
            _log(f"\n[b red]✗ Build failed:[/] {exc}")
            _log(
                "[red]Check the log above for details. "
                "The drive may be in a partial state — re-run to start over.[/]"
            )
            # Re-enable buttons so the user can retry
            self.query_one("#ba_build", Button).disabled = False
            self.query_one("#ba_refresh", Button).disabled = False

        finally:
            self._in_progress = False

    # -----------------------------------------------------------------------
    # Bindings
    # -----------------------------------------------------------------------

    def action_back(self) -> None:
        if not self._in_progress:
            self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
