from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from app.tui.screens.game_cheats import GameCheatsScreen
from app.tui.screens.game_mods import GameModsScreen
from app.tui.screens.game_patches import GamePatchesScreen
from app.tui.screens.game_saves import GameSavesScreen
from app.tui.screens.homebrew import HomebrewScreen
from app.tui.screens.library import LibraryScreen
from app.tui.screens.iso2god_screen import Iso2GodScreen
from app.tui.screens.ftp_browser import FtpBrowserScreen
from app.tui.screens.pipeline import NewGamePipelineScreen
from app.tui.screens.settings import SettingsScreen
from app.tui.screens.trainers import TrainersScreen
from app.tui.screens.transfer_games import TransferGamesScreen
from app.tui.screens.torrent_picker import TorrentPickerScreen
from app.tui.screens.utilities import UtilitiesScreen
from app.tui.widgets.connection_bar import ConnectionBar


class MainMenuScreen(Screen):
    TITLE = "Main Menu"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Vertical(id="main_menu"):
            with Vertical(id="main_menu_buttons"):
                yield Static("[b cyan]x360tm — Xbox 360 Mod Manager[/]")
                yield Static("", id="counts", classes="muted")
                yield Button("My Library", id="m_lib", classes="menu_button", variant="success")
                yield Button("Game Mods", id="m_mods", classes="menu_button", variant="primary")
                yield Button("Homebrew", id="m_hb", classes="menu_button")
                yield Button("Trainers", id="m_tr", classes="menu_button")
                yield Button("Game Saves", id="m_gs", classes="menu_button")
                yield Button("Game Cheats", id="m_gc", classes="menu_button")
                yield Button("Game Patches", id="m_gp", classes="menu_button")
                yield Button("Transfer Games", id="m_tg", classes="menu_button", variant="warning")
                yield Button("ISO → GOD", id="m_iso", classes="menu_button", variant="warning")
                yield Button("Game Torrents", id="m_tor", classes="menu_button", variant="warning")
                yield Button("New Game Processing", id="m_pip", classes="menu_button", variant="warning")
                yield Button("FTP File Browser", id="m_ftp", classes="menu_button", variant="warning")
                yield Button("Utilities", id="m_utils", classes="menu_button")
                yield Button("Settings", id="m_set", classes="menu_button")
                yield Button("Quit", id="m_quit", classes="menu_button", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        db = self.app.db
        library: dict = getattr(self.app, 'library', {})
        lib_note = f"  |  Library: {len(library)} game(s)" if library else ""
        counts = (
            f"Mods: {len(db.game_mods)}  |  Homebrew: {len(db.homebrew)}  |  "
            f"Trainers: {sum(len(g.trainers) for g in db.trainers)}  |  "
            f"Saves: {len(db.game_saves)}  |  Cheats: {len(db.game_cheats)}  |  "
            f"Patches: {len(db.game_patches)}{lib_note}"
        )
        self.query_one("#counts", Static).update(counts)
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status
        bar.set_status(connected=s["connected"], text=s["text"])

    def on_screen_resume(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status
        bar.set_status(connected=s["connected"], text=s["text"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m = {
            "m_lib": LibraryScreen,
            "m_mods": GameModsScreen,
            "m_hb": HomebrewScreen,
            "m_tr": TrainersScreen,
            "m_gs": GameSavesScreen,
            "m_gc": GameCheatsScreen,
            "m_gp": GamePatchesScreen,
            "m_tg": TransferGamesScreen,
            "m_iso": Iso2GodScreen,
            "m_tor": TorrentPickerScreen,
            "m_pip": NewGamePipelineScreen,
            "m_ftp": FtpBrowserScreen,
            "m_utils": UtilitiesScreen,
            "m_set": SettingsScreen,
        }
        cls = m.get(event.button.id)
        if cls:
            self.app.push_screen(cls())
        elif event.button.id == "m_quit":
            self.app.exit()

    def action_quit(self) -> None:
        self.app.exit()
