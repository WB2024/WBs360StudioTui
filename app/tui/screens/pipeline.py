"""New Game Processing — pipeline screen.

Stages:
  0. Extract — detect archives, user picks which to unzip, extract with 7zip
  1. Scan   — find ISOs / GOD containers in torrent download folder
  2. Select — user picks which games to process
  3. Convert — ISO→GOD (skipped if already GOD)
  4. Tidy   — apply local folder-name format
  5. Transfer — send to console (FTP or USB)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)
from textual.widgets.data_table import RowKey

from app.core.ftp_client import FtpClient
from app.core.game_tidy import ALL_FORMATS, FORMAT_NAME_SLASH_TITLE_ID
from app.core.iso2god import Iso2GodError, binary_exists, binary_path, ConversionProgress
from app.core.library_scanner import load_csv_titles
from app.core.pipeline import (
    DiscoveredArchive,
    ExtractionError,
    GameStatus,
    PipelineGame,
    convert_iso_to_god,
    extract_archive,
    find_7zip,
    local_god_rename,
    scan_archives,
    scan_download_folder,
)
from app.core.usb_manager import UsbManager
from app.tui.screens.connection import ConnectionScreen
from app.tui.widgets.status_bar import StatusBar

_CSV_PATH = Path(__file__).parent.parent.parent.parent / "gamelist_xbox360.csv"


# ── Transfer-method modal ────────────────────────────────────────────────────

class TransferMethodModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    TransferMethodModal { align: center middle; }
    #tm_box {
        width: 50; height: auto;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    #tm_box Button { width: 100%; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="tm_box"):
            yield Static("[b]Transfer Method[/b]")
            yield Button("FTP — to Xbox 360", id="tm_ftp", variant="primary")
            yield Button("USB — to mounted drive", id="tm_usb")
            yield Button("Cancel", id="tm_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m: dict[str, str | None] = {
            "tm_ftp": "ftp", "tm_usb": "usb", "tm_cancel": None,
        }
        self.dismiss(m.get(event.button.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class UsbDriveModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    UsbDriveModal { align: center middle; }
    #ud_box {
        width: 60; height: auto;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    #ud_btns Button { margin-right: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="ud_box"):
            yield Static("[b]Choose USB Drive[/b]")
            yield ListView(id="ud_list")
            with Horizontal(id="ud_btns"):
                yield Button("Use Selected", id="ud_ok", variant="primary")
                yield Button("Cancel", id="ud_cancel")

    def on_mount(self) -> None:
        usb = UsbManager()
        lv = self.query_one("#ud_list", ListView)
        for d in usb.detect_drives():
            item = ListItem(Label(d.display))
            item.data = d.mount_point  # type: ignore[attr-defined]
            lv.append(item)
        if not usb.detect_drives():
            lv.append(ListItem(Label("[yellow]No removable drives detected[/]")))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ud_cancel":
            self.dismiss(None)
            return
        lv = self.query_one("#ud_list", ListView)
        if lv.highlighted_child and hasattr(lv.highlighted_child, "data"):
            self.dismiss(lv.highlighted_child.data)  # type: ignore[attr-defined]
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Archive selection modal ────────────────────────────────────────────────

class ArchiveSelectModal(ModalScreen[list[DiscoveredArchive] | None]):
    """Show found archives; user picks which to extract."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ArchiveSelectModal { align: center middle; }
    #arc_box {
        width: 80; height: auto; max-height: 36;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    #arc_table { height: auto; max-height: 20; }
    #arc_btns Button { margin-right: 1; }
    """

    def __init__(self, archives: list[DiscoveredArchive], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._archives = archives
        self._selected: set[int] = set(range(len(archives)))  # all selected by default
        self._row_keys: dict[int, RowKey] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="arc_box"):
            yield Static(
                f"[b]{len(self._archives)} archive(s) found.[/b]  "
                "Select which to extract, then click Extract."
            )
            yield DataTable(id="arc_table", cursor_type="row")
            with Horizontal(id="arc_btns"):
                yield Button("Extract Selected", id="arc_ok", variant="primary")
                yield Button("Select All", id="arc_all")
                yield Button("Select None", id="arc_none")
                yield Button("Skip All", id="arc_cancel")

    def on_mount(self) -> None:
        dt = self.query_one("#arc_table", DataTable)
        dt.add_columns("", "Archive", "Type", "Size")
        self._rebuild()

    def _rebuild(self) -> None:
        dt = self.query_one("#arc_table", DataTable)
        dt.clear()
        self._row_keys.clear()
        for i, arc in enumerate(self._archives):
            check = "[green][x][/green]" if i in self._selected else "[ ]"
            rk = dt.add_row(
                check,
                arc.name,
                arc.ext.lstrip(".").upper(),
                f"{arc.size_mb:.1f} MB",
                key=str(i),
            )
            self._row_keys[i] = rk

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        idx = int(str(event.row_key.value))
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._rebuild()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "arc_ok":
            chosen = [self._archives[i] for i in sorted(self._selected)]
            self.dismiss(chosen if chosen else [])
        elif bid == "arc_all":
            self._selected = set(range(len(self._archives)))
            self._rebuild()
        elif bid == "arc_none":
            self._selected.clear()
            self._rebuild()
        elif bid == "arc_cancel":
            self.dismiss(None)  # skip extraction entirely

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Main screen ──────────────────────────────────────────────────────────────

class NewGamePipelineScreen(Screen):
    TITLE = "New Game Processing"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("r", "scan", "Re-scan", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    # Stage labels shown in the header
    _STAGES = ["Extract", "Scan", "Select", "Convert", "Tidy", "Transfer"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._games: list[PipelineGame] = []
        self._selected: set[int] = set()   # indices into _games
        self._stage: int = 0               # 0=extract,1=scan,2=select,3=convert,4=tidy,5=transfer
        self._row_keys: dict[int, RowKey] = {}   # game index → DataTable row key

    # ── layout ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="pip_stage_bar"):
            for i, label in enumerate(self._STAGES):
                yield Static(label, id=f"pip_stage_{i}", classes="pip_stage")
        yield Static("", id="pip_info")
        with Horizontal(id="pip_actions"):
            yield Button("Scan Folder [R]", id="pip_scan", variant="primary")
            yield Button("Select All", id="pip_sel_all")
            yield Button("Select None", id="pip_sel_none")
            yield Button("Proceed →", id="pip_proceed", variant="success", disabled=True)
            yield Button("Back [Esc]", id="pip_back")
        yield DataTable(id="pip_table", cursor_type="row")
        yield Static("", id="pip_log")
        yield StatusBar(id="status_bar")
        yield Footer()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        dt = self.query_one("#pip_table", DataTable)
        dt.add_columns("", "Game", "Type", "Status")
        self._update_stage_bar()
        self._set_status("Press R or click Scan Folder to begin.")

    # ── stage bar ─────────────────────────────────────────────────────────────

    def _update_stage_bar(self) -> None:
        for i, _ in enumerate(self._STAGES):
            w = self.query_one(f"#pip_stage_{i}", Static)
            if i < self._stage:
                w.add_class("pip_stage_done")
                w.remove_class("pip_stage_active")
            elif i == self._stage:
                w.add_class("pip_stage_active")
                w.remove_class("pip_stage_done")
            else:
                w.remove_class("pip_stage_active", "pip_stage_done")

    # ── table helpers ─────────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        dt = self.query_one("#pip_table", DataTable)
        dt.clear()
        self._row_keys.clear()
        for i, g in enumerate(self._games):
            check = "[green][x][/green]" if i in self._selected else "[ ]"
            status_text = self._status_text(g)
            rk = dt.add_row(check, g.name, g.display_type, status_text, key=str(i))
            self._row_keys[i] = rk

    def _refresh_row(self, idx: int) -> None:
        g = self._games[idx]
        dt = self.query_one("#pip_table", DataTable)
        check = "[green][x][/green]" if idx in self._selected else "[ ]"
        status_text = self._status_text(g)
        rk = self._row_keys.get(idx)
        if rk is not None:
            dt.update_cell(rk, "")
            # DataTable.update_cell takes (row_key, column_key) — use column index
            try:
                dt.update_cell_at((dt.get_row_index(rk), 0), check)
                dt.update_cell_at((dt.get_row_index(rk), 3), status_text)
            except Exception:
                pass

    def _status_text(self, g: PipelineGame) -> str:
        colours = {
            GameStatus.PENDING: "",
            GameStatus.CONVERTING: "[yellow]Converting…[/yellow]",
            GameStatus.CONVERTED: "[cyan]Converted[/cyan]",
            GameStatus.TIDYING: "[yellow]Tidying…[/yellow]",
            GameStatus.TRANSFERRING: "[yellow]Transferring…[/yellow]",
            GameStatus.DONE: "[green]Done[/green]",
            GameStatus.SKIPPED: "[dim]Skipped[/dim]",
            GameStatus.ERROR: f"[red]Error[/red]",
        }
        base = colours.get(g.status, "")
        if g.status == GameStatus.ERROR and g.status_detail:
            base += f": {g.status_detail[:60]}"
        return base or "—"

    # ── status / log ──────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self.query_one("#status_bar", StatusBar).set_text(text)

    def _log(self, text: str) -> None:
        log_w = self.query_one("#pip_log", Static)
        log_w.update(text)

    # ── events ────────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "pip_scan":
            self.action_scan()
        elif bid == "pip_sel_all":
            self._selected = set(range(len(self._games)))
            self._rebuild_table()
            self._update_proceed()
        elif bid == "pip_sel_none":
            self._selected.clear()
            self._rebuild_table()
            self._update_proceed()
        elif bid == "pip_proceed":
            self.run_worker(self._run_pipeline(), exclusive=True)
        elif bid == "pip_back":
            self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle selection when user presses Enter/clicks a row."""
        if event.row_key is None:
            return
        idx = int(str(event.row_key.value))
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._refresh_row(idx)
        self._update_proceed()

    def _update_proceed(self) -> None:
        btn = self.query_one("#pip_proceed", Button)
        btn.disabled = len(self._selected) == 0

    # ── actions ───────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    def action_scan(self) -> None:
        self.run_worker(self._do_scan(), exclusive=True)

    async def _do_scan(self) -> None:
        """Worker: check for archives → optionally extract → scan for games."""
        settings = self.app.settings  # type: ignore[attr-defined]
        folder = settings.torrent_download_folder
        if not folder:
            self._set_status(
                "Torrent download folder not set — configure it in Settings first."
            )
            return

        # ═══ Stage 0: Extract ═════════════════════════════════════════════════
        self._stage = 0
        self._update_stage_bar()
        self._set_status(f"Checking {folder} for archives…")

        archives = scan_archives(folder)
        if archives:
            seven_zip = find_7zip()
            if not seven_zip:
                self._log(
                    "[yellow]Archives found but 7zip not installed — skipping extraction.[/yellow]\n"
                    "Install 7zip (p7zip-full) to enable automatic extraction."
                )
            else:
                chosen: list[DiscoveredArchive] | None = await self.app.push_screen_wait(
                    ArchiveSelectModal(archives)
                )
                if chosen:  # None = skip all, [] = user chose none
                    await self._extract_archives(chosen, Path(folder), seven_zip)

        # ═══ Stage 1: Scan ════════════════════════════════════════════════════
        self._stage = 1
        self._update_stage_bar()
        self._set_status(f"Scanning {folder}…")

        self._games = scan_download_folder(folder)
        self._selected = set(range(len(self._games)))  # default: all selected
        self._rebuild_table()

        if not self._games:
            self._set_status(f"No games found in {folder}.")
            self._log("")
        else:
            isos = sum(1 for g in self._games if g.iso)
            gods = sum(1 for g in self._games if g.god)
            self._set_status(
                f"Found {len(self._games)} game(s): {isos} ISO, {gods} already GOD. "
                f"Select games below then click Proceed."
            )
            self._log("")

        self._stage = 2
        self._update_stage_bar()
        self._update_proceed()

    async def _extract_archives(
        self,
        archives: list[DiscoveredArchive],
        folder: Path,
        seven_zip: str,
    ) -> None:
        """Extract each chosen archive into the download folder."""
        for i, arc in enumerate(archives):
            self._set_status(f"Extracting {i + 1}/{len(archives)}: {arc.name}{arc.ext}…")
            # Extract into the same download folder so game scanner picks it up
            try:
                await extract_archive(
                    archive=arc,
                    dest_dir=folder,
                    seven_zip=seven_zip,
                    on_line=lambda line: self._log(
                        f"[cyan]7zip[/cyan] {line[:120]}"
                    ),
                )
                self._log(f"[green]Extracted:[/green] {arc.name}{arc.ext}")
            except ExtractionError as exc:
                self._log(f"[red]Extraction failed:[/red] {exc}")

    # ── pipeline worker ───────────────────────────────────────────────────────

    async def _run_pipeline(self) -> None:
        settings = self.app.settings  # type: ignore[attr-defined]
        games_to_process = [self._games[i] for i in sorted(self._selected)]

        # ── Ask transfer method ──
        method = await self.app.push_screen_wait(TransferMethodModal())
        if method is None:
            return

        usb_root: str | None = None
        if method == "usb":
            usb_root = await self.app.push_screen_wait(UsbDriveModal())
            if not usb_root:
                return

        # ── Validate iso2god binary for ISO games ──
        iso_games = [g for g in games_to_process if g.iso]
        if iso_games:
            if not binary_exists():
                self._log(
                    "[red]iso2god binary not found.[/red]\n"
                    "Open the ISO→GOD screen once to download it automatically."
                )
                self._set_status("iso2god binary missing — cannot convert ISOs.")
                return
            bin_path = binary_path()
        else:
            bin_path = None

        god_output = Path(settings.local_god_path) if settings.local_god_path else None
        if iso_games and not god_output:
            self._log(
                "[red]Local GOD path not set in Settings.[/red]\n"
                "Set 'Local GOD Path' in Settings so converted games have somewhere to go."
            )
            self._set_status("Local GOD path not configured.")
            return

        # Load CSV for name lookup (tidy step)
        csv_titles: dict[str, str] = {}
        if _CSV_PATH.exists():
            csv_titles = load_csv_titles(_CSV_PATH)

        naming_format = getattr(settings, "god_naming_format", FORMAT_NAME_SLASH_TITLE_ID)

        total = len(games_to_process)

        # ═══ Stage: Convert ═══════════════════════════════════════════════════
        self._stage = 3
        self._update_stage_bar()

        for idx_in_batch, g in enumerate(games_to_process):
            if g.iso:
                g.status = GameStatus.CONVERTING
                self._rebuild_table()
                self._set_status(
                    f"Converting {idx_in_batch + 1}/{total}: {g.name}…"
                )

                def _prog(p: ConversionProgress, _g=g) -> None:
                    detail = f"{p.parts_done}/{p.parts_total}" if p.parts_total else p.stage
                    self._log(
                        f"[yellow]Converting {_g.name}[/yellow] — {detail}"
                    )

                try:
                    assert bin_path is not None
                    assert god_output is not None
                    converted = await convert_iso_to_god(
                        iso=g.iso,
                        god_output_root=god_output,
                        binary_path=bin_path,
                        on_progress=_prog,
                    )
                    g.converted_god = converted
                    g.status = GameStatus.CONVERTED
                except (Iso2GodError, Exception) as exc:
                    g.status = GameStatus.ERROR
                    g.status_detail = str(exc)
                    self._rebuild_table()
                    continue
            else:
                # Already a GOD — pass through
                g.converted_god = g.god
                g.status = GameStatus.CONVERTED

            self._rebuild_table()

        # ═══ Stage: Tidy (local rename) ═══════════════════════════════════════
        self._stage = 4
        self._update_stage_bar()

        for g in games_to_process:
            if g.status == GameStatus.ERROR:
                continue
            assert g.converted_god is not None
            god = g.converted_god

            g.status = GameStatus.TIDYING
            self._rebuild_table()

            # Determine friendly name from TitleID → CSV lookup
            friendly = csv_titles.get(god.title_id.upper(), god.name)
            try:
                g.final_god = local_god_rename(
                    god=god,
                    friendly_name=friendly,
                    target_format=naming_format,
                    god_output_root=god_output,
                )
            except Exception as exc:
                g.status = GameStatus.ERROR
                g.status_detail = f"Tidy failed: {exc}"
                self._rebuild_table()
                continue

        self._rebuild_table()

        # ═══ Stage: Transfer ══════════════════════════════════════════════════
        self._stage = 5
        self._update_stage_bar()

        installer = self.app.installer  # type: ignore[attr-defined]

        # For FTP: acquire a connected client once for the whole batch
        ftp_client: FtpClient | None = None
        if method == "ftp":
            prof = settings.default_profile()
            if prof is None:
                prof = await self.app.push_screen_wait(ConnectionScreen())
            if not prof:
                self._set_status("No connection profile — transfer cancelled.")
                return
            ftp_client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            try:
                await ftp_client.connect()
            except Exception as exc:
                self._set_status(f"FTP connection failed: {exc}")
                return

        transferable = [g for g in games_to_process if g.final_god is not None]
        done_count = 0

        try:
            for idx_in_batch, g in enumerate(transferable):
                g.status = GameStatus.TRANSFERRING
                self._rebuild_table()
                self._set_status(
                    f"Transferring {idx_in_batch + 1}/{len(transferable)}: {g.name}…"
                )
                assert g.final_god is not None

                def _prog(stage: str, cur: int, tot: int, _name: str = g.name) -> None:
                    self._log(f"[yellow]Transferring {_name}[/yellow] — {cur}/{tot} files")

                try:
                    if method == "ftp":
                        assert ftp_client is not None
                        result = await installer.install_god_via_ftp(
                            game=g.final_god,
                            ftp_client=ftp_client,
                            dest_root=settings.game_install_path,
                            progress=_prog,
                        )
                    else:
                        assert usb_root is not None
                        result = await installer.install_god_via_usb(
                            game=g.final_god,
                            usb_root=usb_root,
                            dest_xbox_path=settings.game_install_path,
                            progress=_prog,
                        )

                    if result.success:
                        g.status = GameStatus.DONE
                        done_count += 1
                    else:
                        g.status = GameStatus.ERROR
                        g.status_detail = result.message
                except Exception as exc:
                    g.status = GameStatus.ERROR
                    g.status_detail = str(exc)

                self._rebuild_table()
        finally:
            if ftp_client is not None:
                try:
                    await ftp_client.disconnect()
                except Exception:
                    pass

        # Mark any remaining (error before tidy) as skipped display
        for g in games_to_process:
            if g.status not in (GameStatus.DONE, GameStatus.ERROR):
                g.status = GameStatus.SKIPPED
        self._rebuild_table()

        errors = [g for g in games_to_process if g.status == GameStatus.ERROR]
        self._log(
            f"[green]Pipeline complete:[/green] {done_count} transferred"
            + (f", {len(errors)} error(s)" if errors else "")
        )
        self._set_status(
            f"Done — {done_count}/{len(transferable)} transferred successfully."
        )
        self._stage = 5  # stay on Transfer stage (all complete)
        self._update_stage_bar()
