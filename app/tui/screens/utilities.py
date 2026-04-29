"""Utilities hub screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar


class UtilitiesScreen(Screen):
    TITLE = "Utilities"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with Vertical(id="utils_menu"):
            yield Static("[b cyan]Utilities[/b cyan]")
            yield Static("Console organisation and management tools.", classes="muted")
            yield Button("Game Directory Tidy-up", id="u_tidy", variant="primary", classes="menu_button")
            yield Button("Back [Esc]", id="u_back", classes="menu_button")
        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self.query_one("#status_bar", StatusBar).set_text(
            "Select a utility to get started."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "u_tidy":
            from app.tui.screens.game_tidy import GameTidyScreen
            self.app.push_screen(GameTidyScreen())
        elif event.button.id == "u_back":
            self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
