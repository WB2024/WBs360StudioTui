"""My Library screen — shows discovered Title ID folders and drill-down to content."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from app.config.settings import cache_dir, save_settings
from app.core.ftp_client import FtpClient, FtpConnectionError
from app.core.library_scanner import save_library, scan_library
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar


class LibraryScreen(Screen):
    TITLE = "My Library"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("s", "scan", "Scan", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Horizontal(id="library_layout"):
            with Vertical(id="library_left"):
                with Horizontal(id="library_toolbar"):
                    yield Button("Scan Library [s]", id="scan_btn", variant="primary")
                    yield Static("", id="scan_count", classes="muted")
                yield ListView(id="game_list")
            with Vertical(id="library_right"):
                yield Static("[dim]Select a game to browse its content.[/]", id="game_title")
                yield Static("", id="game_info", classes="muted")
                with Vertical(id="browse_buttons"):
                    yield Button("Trainers", id="browse_trainers", classes="browse_btn")
                    yield Button("Game Saves", id="browse_saves", classes="browse_btn")
                    yield Button("Game Mods", id="browse_mods", classes="browse_btn")
                    yield Button("Game Cheats", id="browse_cheats", classes="browse_btn")
                    yield Button("Game Patches", id="browse_patches", classes="browse_btn")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_conn_bar()
        self._populate_list()
        self._set_detail(None)

    def _refresh_conn_bar(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    def _populate_list(self) -> None:
        library: dict[str, str] = self.app.library  # type: ignore[attr-defined]
        lv = self.query_one("#game_list", ListView)
        lv.clear()
        db = self.app.db  # type: ignore[attr-defined]
        for tid in sorted(library.keys()):
            name = db.resolve_game_title(tid)
            item = ListItem(Label(f"[b]{name}[/]  [dim]{tid}[/dim]"))
            item._tid = tid  # type: ignore[attr-defined]
            lv.append(item)
        count = len(library)
        self.query_one("#scan_count", Static).update(
            f"  {count} game{'s' if count != 1 else ''} in library"
        )

    def _set_detail(self, tid: str | None) -> None:
        db = self.app.db  # type: ignore[attr-defined]
        library: dict[str, str] = self.app.library  # type: ignore[attr-defined]
        if tid is None:
            self.query_one("#game_title", Static).update("[dim]Select a game to browse its content.[/]")
            self.query_one("#game_info", Static).update("")
            for btn_id in ("browse_trainers", "browse_saves", "browse_mods", "browse_cheats", "browse_patches"):
                self.query_one(f"#{btn_id}", Button).disabled = True
            self._selected_tid = None
        else:
            name = db.resolve_game_title(tid)
            ftp_path = library.get(tid, "?")
            self.query_one("#game_title", Static).update(f"[b cyan]{name}[/b cyan]")
            self.query_one("#game_info", Static).update(f"Title ID: {tid}\nFTP: {ftp_path}")
            for btn_id in ("browse_trainers", "browse_saves", "browse_mods", "browse_cheats", "browse_patches"):
                self.query_one(f"#{btn_id}", Button).disabled = False
            self._selected_tid = tid

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is not None and hasattr(item, "_tid"):
            self._set_detail(item._tid)  # type: ignore[attr-defined]

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        tid = getattr(self, "_selected_tid", None)

        if bid == "scan_btn":
            await self._do_scan()
            return

        if tid is None:
            return

        # Import here to avoid circular imports.
        from app.tui.screens.game_cheats import GameCheatsScreen
        from app.tui.screens.game_mods import GameModsScreen
        from app.tui.screens.game_patches import GamePatchesScreen
        from app.tui.screens.game_saves import GameSavesScreen
        from app.tui.screens.trainers import TrainersScreen

        screen_map = {
            "browse_trainers": TrainersScreen,
            "browse_saves": GameSavesScreen,
            "browse_mods": GameModsScreen,
            "browse_cheats": GameCheatsScreen,
            "browse_patches": GamePatchesScreen,
        }
        cls = screen_map.get(bid)
        if cls:
            self.app.push_screen(cls(title_id_filter=tid))  # type: ignore[call-arg]

    async def _do_scan(self) -> None:
        app = self.app  # type: ignore[attr-defined]
        settings = app.settings
        if not settings.game_paths:
            self.query_one("#status_bar", StatusBar).set_text(
                "No game paths configured. Add them in Settings → Game Library Paths."
            )
            return
        if not app.connection_status.get("connected"):
            self.query_one("#status_bar", StatusBar).set_text(
                "Not connected to Xbox. Connect first via Settings → Connection Profiles."
            )
            return

        self.query_one("#scan_btn", Button).disabled = True
        self.query_one("#status_bar", StatusBar).set_text("Scanning library…")
        self.run_worker(self._scan_worker(), exclusive=True)

    async def _scan_worker(self) -> None:
        app = self.app  # type: ignore[attr-defined]
        settings = app.settings
        prof = settings.default_profile()
        if prof is None:
            self._scan_done({}, "No connection profile configured.")
            return

        def progress(msg: str) -> None:
            self.query_one("#status_bar", StatusBar).set_text(msg)

        try:
            client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            await client.connect()
            try:
                library = await scan_library(
                    client,
                    settings.game_paths,
                    settings.game_scan_depth,
                    progress_callback=progress,
                )
            finally:
                await client.disconnect()
        except FtpConnectionError as e:
            self._scan_done({}, f"FTP error: {e}")
            return
        except Exception as e:
            self._scan_done({}, f"Scan failed: {e}")
            return

        save_library(library, cache_dir())
        app.library = library
        self._scan_done(library, f"Scan complete — {len(library)} game(s) found.")

    def _scan_done(self, library: dict[str, str], message: str) -> None:
        self.query_one("#scan_btn", Button).disabled = False
        self.query_one("#status_bar", StatusBar).set_text(message)
        self._populate_list()

    # --- Actions ---
    def action_scan(self) -> None:
        self.run_worker(self._do_scan(), exclusive=True)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
