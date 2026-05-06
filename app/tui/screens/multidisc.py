"""Multi-Disc Game Setup screen.

Handles the two-disc install pattern used by some Xbox 360 games (e.g. games
that ship with a separate Install Disc and a Play Disc):

  Step 1 — Extract the Install Disc using extract-xiso.
            The extracted content/ folder is placed on the console via FTP
            (optional, requires a configured FTP profile).

  Step 2 — Convert the Play Disc ISO to GOD format using iso2god.
            The resulting GOD files go to the local output folder and can
            then be transferred to the console via Transfer Games.

This is a direct port of the multi-disc feature from X360Forge, adapted for
the async Textual TUI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Rule, Select, Static, Switch

import app.core.extract_xiso as extract_xiso_core
import app.core.iso2god as iso2god_core
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


class MultiDiscScreen(Screen):
    """Guided multi-disc game setup: extract install disc + convert play disc."""

    TITLE = "Multi-Disc Game Setup"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    # Sentinel used by Select when nothing is selected yet
    _NO_SELECTION = Select.BLANK

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._iso_files: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with VerticalScroll(id="md_scroll"):
            yield Static("[b cyan]Multi-Disc Game Setup[/b cyan]")
            yield Static(
                "[dim]For games with a separate Install Disc and Play Disc.\n"
                "Step 1 extracts the install content.  "
                "Step 2 converts the play disc to GOD format.[/]"
            )
            yield Rule()

            # ── Step 1: ISO folder ────────────────────────────────────────────
            yield Static("\n[b]Step 1 — Select ISO Folder[/b]")
            yield Static("[dim]The folder containing both disc ISOs.[/]")
            yield Input(
                placeholder="Folder containing the two ISO files",
                id="md_iso_folder",
            )
            with Horizontal(classes="xf_btn_row"):
                yield Button("Scan for ISOs", id="md_scan", variant="primary")
            yield Static("", id="md_scan_status", classes="muted")

            yield Rule()

            # ── Step 2: Disc assignment ───────────────────────────────────────
            yield Static("\n[b]Step 2 — Assign Discs[/b]")
            yield Static(
                "[dim]Select which ISO is the Install Disc and which is the Play Disc.[/]"
            )
            yield Static("Install Disc:")
            yield Select(
                options=[("— scan first —", "_none")],
                id="md_install_select",
                value="_none",
            )
            yield Static("Play Disc:")
            yield Select(
                options=[("— scan first —", "_none")],
                id="md_play_select",
                value="_none",
            )

            yield Rule()

            # ── Step 3: Output folder ─────────────────────────────────────────
            yield Static("\n[b]Step 3 — Output Folder[/b]")
            yield Static(
                "[dim]Where the extracted content folder and GOD files will be placed.[/]"
            )
            yield Input(
                placeholder="Output folder for extracted content and GOD files",
                id="md_output_folder",
            )

            yield Rule()

            # ── FTP option ────────────────────────────────────────────────────
            yield Static("\n[b]Optional — FTP Transfer content/ Folder[/b]")
            yield Static(
                "[dim]After extracting the install disc, upload the content/ folder\n"
                "to the console via FTP.  Uses the default FTP profile from Settings.\n"
                "Toggle the switch ON to enable.[/]"
            )
            with Horizontal(classes="xf_path_row"):
                yield Switch(id="md_ftp_switch", value=False)
                yield Static("  Transfer content/ via FTP after extraction")

            yield Rule()

            # ── Run ───────────────────────────────────────────────────────────
            with Horizontal(classes="xf_btn_row"):
                yield Button(
                    "Run Multi-Disc Setup",
                    id="md_run",
                    variant="success",
                )
                yield Button("Back [Esc]", id="md_back")

        yield StatusBar(id="status_bar")
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self._set_status(
            "Enter the ISO folder and click Scan to detect disc files."
        )
        # Pre-fill from settings if set
        settings = self.app.settings  # type: ignore[attr-defined]
        if settings.xforge_source_path:
            self.query_one("#md_iso_folder", Input).value = settings.xforge_source_path
        if settings.xforge_output_path:
            self.query_one("#md_output_folder", Input).value = settings.xforge_output_path

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(text)
        except Exception:
            pass

    # ── Button dispatch ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "md_scan":
            self.app.run_worker(self._scan_isos(), exclusive=False)
        elif bid == "md_run":
            self.app.run_worker(self._run_multidisc(), exclusive=True)
        elif bid == "md_back":
            self.app.pop_screen()


    async def _scan_isos(self) -> None:
        folder = self.query_one("#md_iso_folder", Input).value.strip()
        if not folder or not Path(folder).is_dir():
            self.query_one("#md_scan_status", Static).update(
                "[red]Folder not found.[/]"
            )
            self._set_status("Error: ISO folder not found.")
            return

        iso_files = sorted(
            [f.name for f in Path(folder).iterdir() if f.suffix.lower() == ".iso"]
        )

        if not iso_files:
            self.query_one("#md_scan_status", Static).update(
                "[red]No .iso files found in that folder.[/]"
            )
            self._set_status("No ISO files found.")
            return

        self._iso_files = iso_files
        options = [(name, name) for name in iso_files]

        install_sel = self.query_one("#md_install_select", Select)
        play_sel = self.query_one("#md_play_select", Select)
        install_sel.set_options(options)
        play_sel.set_options(options)

        # Auto-assign if exactly 2 ISOs
        if len(iso_files) == 2:
            install_sel.value = iso_files[0]
            play_sel.value = iso_files[1]

        count_text = f"[green]Found {len(iso_files)} ISO file(s).[/]"
        self.query_one("#md_scan_status", Static).update(count_text)
        self._set_status(f"Found {len(iso_files)} ISO(s) — assign discs then click Run.")

    # ── Main multi-disc worker ────────────────────────────────────────────────

    async def _run_multidisc(self) -> None:
        iso_folder = self.query_one("#md_iso_folder", Input).value.strip()
        output_dir = self.query_one("#md_output_folder", Input).value.strip()
        install_iso = self.query_one("#md_install_select", Select).value
        play_iso = self.query_one("#md_play_select", Select).value
        do_ftp = self.query_one("#md_ftp_switch", Switch).value

        # ── Validation ────────────────────────────────────────────────────────
        if not iso_folder or not Path(iso_folder).is_dir():
            self._set_status("Error: ISO folder not found.")
            return
        if install_iso in (Select.BLANK, "_none", None):
            self._set_status("Error: select the Install Disc.")
            return
        if play_iso in (Select.BLANK, "_none", None):
            self._set_status("Error: select the Play Disc.")
            return
        if str(install_iso) == str(play_iso):
            self._set_status("Error: Install Disc and Play Disc must be different.")
            return
        if not output_dir or not Path(output_dir).is_dir():
            self._set_status("Error: output folder not found.")
            return

        settings = self.app.settings  # type: ignore[attr-defined]

        # Check extract-xiso binary
        xiso_binary = extract_xiso_core.binary_path(settings.extract_xiso_binary_path)
        if not xiso_binary.is_file():
            self._set_status(
                "extract-xiso binary not found — configure in Settings → X360Forge Tools."
            )
            return

        # Check iso2god binary
        god_binary = iso2god_core.binary_path()
        if not god_binary.is_file():
            self._set_status(
                "iso2god binary not found — open ISO → GOD screen to download it first."
            )
            return

        install_iso_path = Path(iso_folder) / str(install_iso)
        play_iso_path = Path(iso_folder) / str(play_iso)

        # ── Open progress modal ───────────────────────────────────────────────
        modal = ProgressModal("Multi-Disc Game Setup")
        await self.app.push_screen(modal)

        # ── STEP 1: Extract install disc ──────────────────────────────────────
        modal.set_stage("Step 1/2  —  Extracting install disc...", 0, 0)
        modal.set_detail(f"Source: {install_iso_path.name}")

        try:
            await extract_xiso_core.extract_iso(
                iso_path=install_iso_path,
                output_dir=Path(output_dir),
                binary=xiso_binary,
                on_line=lambda line: modal.set_detail(line),
            )
        except extract_xiso_core.ExtractXisoError as e:
            modal.set_done(f"Extraction failed: {e}", success=False)
            return

        # Find the extracted content/ folder
        stem = install_iso_path.stem
        content_folder = Path(output_dir) / stem / "content"
        if not content_folder.is_dir():
            # Try the output dir directly (some builds extract flat)
            content_folder = Path(output_dir) / "content"

        if content_folder.is_dir():
            modal.set_detail(f"Content folder: {content_folder}")

        # ── STEP 1b: Optional FTP upload of content/ ─────────────────────────
        if do_ftp and content_folder.is_dir():
            modal.set_stage("Step 1b  —  Uploading content/ via FTP...", 0, 0)
            prof = settings.default_profile()
            if prof is None:
                from app.tui.screens.connection import ConnectionScreen
                prof = await self.app.push_screen_wait(ConnectionScreen())
            if prof:
                from app.core.ftp_client import FtpClient
                client = FtpClient(prof.host, prof.port, prof.username, prof.password)
                try:
                    await client.connect()
                    remote_dest = "/Hdd1/Content/0000000000000000/"
                    await client.upload_directory(
                        local_path=str(content_folder),
                        remote_path=remote_dest,
                        progress_callback=lambda done, total, rel: modal.set_stage(
                            f"Uploading: {rel}",
                            done,
                            total,
                        ),
                    )
                    await client.disconnect()
                    modal.set_detail("Content folder uploaded to console.")
                except Exception as e:
                    modal.set_detail(
                        f"FTP upload failed: {e}\n"
                        f"Copy manually from: {content_folder}"
                    )
            else:
                modal.set_detail("FTP skipped — no profile selected.")

        # ── STEP 2: Convert play disc to GOD ──────────────────────────────────
        modal.set_stage("Step 2/2  —  Converting play disc to GOD...", 0, 0)
        modal.set_detail(f"Source: {play_iso_path.name}")

        prog = iso2god_core.ConversionProgress()

        def _on_god_progress(p: iso2god_core.ConversionProgress) -> None:
            nonlocal prog
            prog = p
            if p.parts_total > 0:
                modal.set_stage(
                    f"Step 2/2  —  Writing GOD parts...",
                    p.parts_done,
                    p.parts_total,
                )
            else:
                modal.set_stage(f"Step 2/2  —  GOD: {p.stage}...", 0, 0)

        try:
            final = await iso2god_core.convert_iso(
                iso_path=play_iso_path,
                dest_dir=Path(output_dir),
                binary=god_binary,
                num_threads=1,
                trim=True,
                on_progress=_on_god_progress,
            )

            lines = ["Multi-disc setup complete!"]
            if final.game_name:
                lines.append(f"Game: {final.game_name}")
            if content_folder.is_dir():
                lines.append(f"Content folder: {content_folder}")
            lines.append(f"GOD output: {output_dir}")
            if do_ftp:
                lines.append("Content folder was transferred to console via FTP.")

            modal.set_done("\n".join(lines), success=True)
            self._set_status("Multi-disc setup complete.")

        except iso2god_core.Iso2GodError as e:
            modal.set_done(f"GOD conversion failed: {e}", success=False)
            self._set_status(f"Error: {e}")

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
