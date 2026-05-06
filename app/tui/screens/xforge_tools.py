"""X360Forge Tools screen.

A scrollable hub screen providing access to all X360Forge ISO utilities:
  - Extract ISOs → Game Folders     (extract-xiso -x)
  - Create ISOs from Game Folders   (extract-xiso -c)
  - Fix ISO — abgx360               (abgx360 --af3 -p -s -o)
  - GOD → ISO                       (god2iso)
  - ISO → GOD                       (redirect to existing Iso2GodScreen)

Each section has inline Input fields for paths and a Run button.  An optional
"Browse" button shells out to zenity (Linux) if available, falling back
gracefully if not installed.

Binary paths are read from Settings → X360Forge Tools.  A status section at
the bottom lets users check and configure the binary paths.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Rule, Static

import app.core.abgx360 as abgx360_core
import app.core.extract_xiso as extract_xiso_core
import app.core.god2iso as god2iso_core
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


# ── Zenity helper ─────────────────────────────────────────────────────────────

async def _pick_via_zenity(title: str, directory: bool = True) -> str | None:
    """Try to open a zenity file/folder picker.  Returns path string or None."""
    args = ["zenity", "--file-selection", f"--title={title}"]
    if directory:
        args.append("--directory")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip() or None
    except FileNotFoundError:
        pass
    return None


# ── Main screen ───────────────────────────────────────────────────────────────

class XForgeToolsScreen(Screen):
    """Scrollable X360Forge ISO utility hub."""

    TITLE = "X360Forge Tools"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with VerticalScroll(id="xforge_scroll"):

            yield Static("[b cyan]X360Forge Tools[/b cyan]")
            yield Static(
                "ISO and game folder utilities ported from X360Forge.",
                classes="muted",
            )
            yield Rule()

            # ── Extract ISOs → Game Folders ───────────────────────────────────
            yield Static("\n[b]Extract ISOs → Game Folders[/b]")
            yield Static(
                "[dim]Extracts every .iso found in the source folder into the output folder.[/]"
            )
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Source folder containing .iso files",
                    id="extract_src",
                )
                yield Button("Browse", id="browse_extract_src", classes="xf_browse")
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Output folder for extracted game folders",
                    id="extract_out",
                )
                yield Button("Browse", id="browse_extract_out", classes="xf_browse")
            with Horizontal(classes="xf_btn_row"):
                yield Button(
                    "Extract ISOs → Game Folders",
                    id="run_extract",
                    variant="success",
                )
                yield Button(
                    "Extract + Delete ISOs  ⚠",
                    id="run_extract_del",
                    variant="error",
                )

            yield Rule()

            # ── Create ISOs from Game Folders ─────────────────────────────────
            yield Static("\n[b]Create ISOs from Game Folders[/b]")
            yield Static(
                "[dim]Scans the source folder for game directories (containing .xex/.xbe) "
                "and creates an ISO for each one.[/]"
            )
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Source folder containing game directories",
                    id="create_src",
                )
                yield Button("Browse", id="browse_create_src", classes="xf_browse")
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Output folder for created ISO files",
                    id="create_out",
                )
                yield Button("Browse", id="browse_create_out", classes="xf_browse")
            with Horizontal(classes="xf_btn_row"):
                yield Button(
                    "Create ISOs from Game Folders",
                    id="run_create",
                    variant="primary",
                )

            yield Rule()

            # ── Fix ISO — abgx360 ─────────────────────────────────────────────
            yield Static("\n[b]Fix ISO — abgx360[/b]")
            yield Static(
                "[dim]Runs abgx360 --af3 -p -s -o on the selected ISO file to verify "
                "and patch stealth / topology data.[/]"
            )
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Path to the .iso file to fix",
                    id="fix_iso_path",
                )
                yield Button("Browse", id="browse_fix_iso", classes="xf_browse")
            with Horizontal(classes="xf_btn_row"):
                yield Button("Fix ISO  —  abgx360", id="run_fix", variant="warning")

            yield Rule()

            # ── GOD → ISO ─────────────────────────────────────────────────────
            yield Static("\n[b]GOD → ISO[/b]")
            yield Static(
                "[dim]Converts a Games on Demand container back to a standard Xbox 360 ISO.\n"
                "Select the GOD container file (the file with no extension inside the TitleID folder).[/]"
            )
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Path to GOD container file (no extension)",
                    id="god_file_path",
                )
                yield Button("Browse", id="browse_god_file", classes="xf_browse")
            with Horizontal(classes="xf_path_row"):
                yield Input(
                    placeholder="Output folder for the ISO file",
                    id="god_out",
                )
                yield Button("Browse", id="browse_god_out", classes="xf_browse")
            with Horizontal(classes="xf_btn_row"):
                yield Button("GOD → ISO", id="run_god2iso", variant="primary")

            yield Rule()

            # ── ISO → GOD (redirect) ──────────────────────────────────────────
            yield Static("\n[b]ISO → GOD[/b]")
            yield Static(
                "[dim]Opens the full ISO → GOD converter screen (existing feature).[/]"
            )
            with Horizontal(classes="xf_btn_row"):
                yield Button(
                    "Open ISO → GOD Converter",
                    id="open_iso2god",
                    variant="default",
                )

            yield Rule()

            # ── Binary status ─────────────────────────────────────────────────
            yield Static("\n[b]Binary Status[/b]")
            yield Static(
                "[dim]These tools require external binaries.  Set the paths in "
                "Settings → X360Forge Tools, or place the binaries in "
                "~/.local/share/x360tm/tools/.[/]"
            )
            yield Static("", id="binary_status")

            yield Rule()
            with Horizontal(classes="xf_btn_row"):
                yield Button("Back [Esc]", id="xf_back")

        yield StatusBar(id="status_bar")
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self._pre_fill_paths()
        self._refresh_binary_status()

    def _pre_fill_paths(self) -> None:
        """Pre-populate path inputs from Settings defaults."""
        settings = self.app.settings  # type: ignore[attr-defined]
        if settings.xforge_source_path:
            self.query_one("#extract_src", Input).value = settings.xforge_source_path
            self.query_one("#create_src", Input).value = settings.xforge_source_path
        if settings.xforge_output_path:
            self.query_one("#extract_out", Input).value = settings.xforge_output_path
            self.query_one("#create_out", Input).value = settings.xforge_output_path
            self.query_one("#god_out", Input).value = settings.xforge_output_path

    def _refresh_binary_status(self) -> None:
        settings = self.app.settings  # type: ignore[attr-defined]
        lines = []
        for label, mod, key in [
            ("extract-xiso", extract_xiso_core, settings.extract_xiso_binary_path),
            ("god2iso",      god2iso_core,       settings.god2iso_binary_path),
            ("abgx360",      abgx360_core,       settings.abgx360_binary_path),
        ]:
            if mod.binary_exists(key):
                lines.append(f"[green]✓ {label}[/]  {mod.binary_path(key)}")
            else:
                lines.append(f"[red]✗ {label} — not found[/]  (configure in Settings → X360Forge Tools)")
        self.query_one("#binary_status", Static).update("\n".join(lines))

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(text)
        except Exception:
            pass

    # ── Button dispatch ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        # Browse buttons — async, use run_worker
        if bid == "browse_extract_src":
            self.app.run_worker(self._browse("#extract_src", "Select ISO source folder", True), exclusive=False)
        elif bid == "browse_extract_out":
            self.app.run_worker(self._browse("#extract_out", "Select output folder", True), exclusive=False)
        elif bid == "browse_create_src":
            self.app.run_worker(self._browse("#create_src", "Select game folders source", True), exclusive=False)
        elif bid == "browse_create_out":
            self.app.run_worker(self._browse("#create_out", "Select output folder", True), exclusive=False)
        elif bid == "browse_fix_iso":
            self.app.run_worker(self._browse("#fix_iso_path", "Select ISO file to fix", False), exclusive=False)
        elif bid == "browse_god_file":
            self.app.run_worker(self._browse("#god_file_path", "Select GOD container file", False), exclusive=False)
        elif bid == "browse_god_out":
            self.app.run_worker(self._browse("#god_out", "Select output folder", True), exclusive=False)

        # Run buttons
        elif bid == "run_extract":
            self.app.run_worker(self._run_extract(delete_after=False), exclusive=True)
        elif bid == "run_extract_del":
            self.app.run_worker(self._run_extract(delete_after=True), exclusive=True)
        elif bid == "run_create":
            self.app.run_worker(self._run_create(), exclusive=True)
        elif bid == "run_fix":
            self.app.run_worker(self._run_fix(), exclusive=True)
        elif bid == "run_god2iso":
            self.app.run_worker(self._run_god2iso(), exclusive=True)
        elif bid == "open_iso2god":
            from app.tui.screens.iso2god_screen import Iso2GodScreen
            self.app.push_screen(Iso2GodScreen())
        elif bid == "xf_back":
            self.app.pop_screen()

    # ── Browse helper ─────────────────────────────────────────────────────────

    async def _browse(self, input_id: str, title: str, directory: bool) -> None:
        path = await _pick_via_zenity(title, directory=directory)
        if path:
            self.query_one(input_id, Input).value = path
        else:
            self._set_status(
                "Browse: zenity not found or cancelled — type the path directly."
            )

    # ── Workers ───────────────────────────────────────────────────────────────

    async def _run_extract(self, delete_after: bool) -> None:
        src = self.query_one("#extract_src", Input).value.strip()
        out = self.query_one("#extract_out", Input).value.strip()

        if not src or not Path(src).is_dir():
            self._set_status("Error: source folder not found.")
            return
        if not out or not Path(out).is_dir():
            self._set_status("Error: output folder not found.")
            return

        settings = self.app.settings  # type: ignore[attr-defined]
        binary = extract_xiso_core.binary_path(settings.extract_xiso_binary_path)
        if not binary.is_file():
            self._set_status(
                "extract-xiso binary not found — set the path in Settings → X360Forge Tools."
            )
            return

        iso_files = [f for f in Path(src).iterdir() if f.suffix.lower() == ".iso"]
        if not iso_files:
            self._set_status("No .iso files found in the source folder.")
            return

        action = "Extract + Delete" if delete_after else "Extract"
        modal = ProgressModal(f"{action} ISOs → Game Folders")
        await self.app.push_screen(modal)

        errors: list[str] = []
        for i, iso in enumerate(iso_files, 1):
            modal.set_stage(f"[{i}/{len(iso_files)}] {iso.name}", i - 1, len(iso_files))
            try:
                await extract_xiso_core.extract_iso(
                    iso_path=iso,
                    output_dir=Path(out),
                    binary=binary,
                    on_line=lambda line: modal.set_detail(line),
                )
                if delete_after:
                    iso.unlink()
            except extract_xiso_core.ExtractXisoError as e:
                errors.append(f"{iso.name}: {e}")

        if errors:
            modal.set_done(
                f"Finished with {len(errors)} error(s):\n" + "\n".join(errors),
                success=False,
            )
        else:
            verb = "extracted and deleted" if delete_after else "extracted"
            modal.set_done(
                f"{len(iso_files)} ISO(s) {verb} successfully → {out}",
                success=True,
            )
        self._refresh_binary_status()

    async def _run_create(self) -> None:
        src = self.query_one("#create_src", Input).value.strip()
        out = self.query_one("#create_out", Input).value.strip()

        if not src or not Path(src).is_dir():
            self._set_status("Error: source folder not found.")
            return
        if not out or not Path(out).is_dir():
            self._set_status("Error: output folder not found.")
            return

        settings = self.app.settings  # type: ignore[attr-defined]
        binary = extract_xiso_core.binary_path(settings.extract_xiso_binary_path)
        if not binary.is_file():
            self._set_status(
                "extract-xiso binary not found — set the path in Settings → X360Forge Tools."
            )
            return

        # Find game dirs — directories that contain a .xex or .xbe at any depth
        game_dirs: list[Path] = []
        for d in Path(src).iterdir():
            if d.is_dir():
                if (
                    any(d.rglob("*.xex"))
                    or any(d.rglob("*.xbe"))
                    or any(d.rglob("default.xex"))
                ):
                    game_dirs.append(d)

        if not game_dirs:
            self._set_status("No game directories found (need .xex/.xbe inside).")
            return

        modal = ProgressModal("Create ISOs from Game Folders")
        await self.app.push_screen(modal)

        errors: list[str] = []
        for i, game_dir in enumerate(game_dirs, 1):
            modal.set_stage(f"[{i}/{len(game_dirs)}] {game_dir.name}", i - 1, len(game_dirs))
            output_iso = Path(out) / f"{game_dir.name}.iso"
            try:
                await extract_xiso_core.create_iso(
                    game_dir=game_dir,
                    output_iso=output_iso,
                    binary=binary,
                    on_line=lambda line: modal.set_detail(line),
                )
            except extract_xiso_core.ExtractXisoError as e:
                errors.append(f"{game_dir.name}: {e}")

        if errors:
            modal.set_done(
                f"Finished with {len(errors)} error(s):\n" + "\n".join(errors),
                success=False,
            )
        else:
            modal.set_done(
                f"{len(game_dirs)} ISO(s) created successfully → {out}",
                success=True,
            )

    async def _run_fix(self) -> None:
        iso_path = self.query_one("#fix_iso_path", Input).value.strip()

        if not iso_path or not Path(iso_path).is_file():
            self._set_status("Error: ISO file not found.")
            return

        settings = self.app.settings  # type: ignore[attr-defined]
        binary = abgx360_core.binary_path(settings.abgx360_binary_path)
        if not binary.is_file():
            self._set_status(
                "abgx360 binary not found — set the path in Settings → X360Forge Tools."
            )
            return

        modal = ProgressModal(f"Fix ISO — abgx360: {Path(iso_path).name}")
        await self.app.push_screen(modal)
        modal.set_stage("Running abgx360...", 0, 0)

        try:
            await abgx360_core.fix_iso(
                iso_path=iso_path,
                binary=binary,
                on_line=lambda line: modal.set_detail(line),
            )
            modal.set_done(f"abgx360 finished: {Path(iso_path).name}", success=True)
        except abgx360_core.Abgx360Error as e:
            modal.set_done(str(e), success=False)

    async def _run_god2iso(self) -> None:
        god_file = self.query_one("#god_file_path", Input).value.strip()
        out = self.query_one("#god_out", Input).value.strip()

        if not god_file or not Path(god_file).is_file():
            self._set_status("Error: GOD container file not found.")
            return
        if not out or not Path(out).is_dir():
            self._set_status("Error: output folder not found.")
            return

        settings = self.app.settings  # type: ignore[attr-defined]
        binary = god2iso_core.binary_path(settings.god2iso_binary_path)
        if not binary.is_file():
            self._set_status(
                "god2iso binary not found — set the path in Settings → X360Forge Tools."
            )
            return

        name = Path(god_file).name
        modal = ProgressModal(f"GOD → ISO: {name}")
        await self.app.push_screen(modal)
        modal.set_stage("Converting...", 0, 0)

        try:
            await god2iso_core.convert_god(
                god_file=god_file,
                output_dir=out,
                binary=binary,
                on_line=lambda line: modal.set_detail(line),
            )
            modal.set_done(f"Conversion complete → {out}", success=True)
        except god2iso_core.God2IsoError as e:
            modal.set_done(str(e), success=False)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
