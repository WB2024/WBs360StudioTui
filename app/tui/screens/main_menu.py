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
from app.tui.screens.settings import SettingsScreen
from app.tui.screens.trainers import TrainersScreen
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
                yield Button("Game Mods", id="m_mods", classes="menu_button", variant="primary")
                yield Button("Homebrew", id="m_hb", classes="menu_button")
                yield Button("Trainers", id="m_tr", classes="menu_button")
                yield Button("Game Saves", id="m_gs", classes="menu_button")
                yield Button("Game Cheats", id="m_gc", classes="menu_button")
                yield Button("Game Patches", id="m_gp", classes="menu_button")
                yield Button("Settings", id="m_set", classes="menu_button")
                yield Button("Quit", id="m_quit", classes="menu_button", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        db = self.app.db
        counts = (
            f"Mods: {len(db.game_mods)}  |  Homebrew: {len(db.homebrew)}  |  "
            f"Trainers: {sum(len(g.trainers) for g in db.trainers)}  |  "
            f"Saves: {len(db.game_saves)}  |  Cheats: {len(db.game_cheats)}  |  "
            f"Patches: {len(db.game_patches)}"
        )
        self.query_one("#counts", Static).update(counts)
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status
        bar.set_status(connected=s["connected"], text=s["text"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m = {
            "m_mods": GameModsScreen,
            "m_hb": HomebrewScreen,
            "m_tr": TrainersScreen,
            "m_gs": GameSavesScreen,
            "m_gc": GameCheatsScreen,
            "m_gp": GamePatchesScreen,
            "m_set": SettingsScreen,
        }
        cls = m.get(event.button.id)
        if cls:
            self.app.push_screen(cls())
        elif event.button.id == "m_quit":
            self.app.exit()

    def action_quit(self) -> None:
        self.app.exit()
