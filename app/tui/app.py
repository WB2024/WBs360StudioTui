"""Top-level Textual App."""
from __future__ import annotations

from pathlib import Path

from textual.app import App

from app.config.settings import Settings, load_settings
from app.core.database import DatabaseManager
from app.core.installer import Installer
from app.core.logging_setup import setup_logging
from app.tui.screens.splash import SplashScreen


class X360TuiApp(App):
    CSS_PATH = "styles/app.tcss"
    TITLE = "x360tm"

    def __init__(self) -> None:
        super().__init__()
        setup_logging()
        self.settings: Settings = load_settings()
        self.db: DatabaseManager = DatabaseManager()
        self.installer: Installer = Installer()
        self.connection_status: dict = {"connected": False, "text": "Not connected"}

    def on_mount(self) -> None:
        self.push_screen(SplashScreen())

    def set_connection_status(self, *, connected: bool, host: str = "") -> None:
        self.connection_status = {
            "connected": connected,
            "text": host if connected else "Not connected",
        }
        # Update any visible ConnectionBar widgets
        from app.tui.widgets.connection_bar import ConnectionBar
        for bar in self.query(ConnectionBar):
            bar.set_status(connected=connected, text=self.connection_status["text"])
