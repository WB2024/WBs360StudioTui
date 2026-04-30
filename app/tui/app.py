"""Top-level Textual App."""
from __future__ import annotations

import sys
from pathlib import Path

from textual.app import App

from app.config.settings import Settings, load_settings, cache_dir
from app.core.database import DatabaseManager
from app.core.installer import Installer
from app.core.library_scanner import load_library
from app.core.logging_setup import setup_logging
from app.tui.screens.splash import SplashScreen


class X360TuiApp(App):
    CSS_PATH = "styles/app.tcss"
    TITLE = "x360tm"

    def __init__(self) -> None:
        # When running as a PyInstaller bundle, CSS_PATH is resolved relative
        # to this file via inspect.getfile().  In a frozen onefile executable
        # the source is not on disk, so we set _BASE_PATH explicitly to the
        # location of this module inside _MEIPASS.
        if getattr(sys, "frozen", False):
            self._BASE_PATH = str(
                Path(sys._MEIPASS) / "app" / "tui" / "app.py"  # type: ignore[attr-defined]
            )
        super().__init__()
        setup_logging()
        self.settings: Settings = load_settings()
        self.db: DatabaseManager = DatabaseManager()
        self.installer: Installer = Installer()
        self.connection_status: dict = {"connected": False, "text": "Not connected"}
        # Library: {TITLE_ID_UPPER: ftp_path}, populated by library scan.
        self.library: dict[str, str] = load_library(cache_dir())

    def on_mount(self) -> None:
        self.push_screen(SplashScreen())
        if self.settings.auto_update:
            # Delay the background check so the UI has time to draw first
            self.set_timer(5.0, self._trigger_update_check)

    def _trigger_update_check(self) -> None:
        self.run_worker(self._auto_update_worker(), exclusive=False)

    async def _auto_update_worker(self) -> None:
        import app as app_mod
        from app.core.updater import check_for_update
        try:
            info = await check_for_update(self.settings.update_channel, app_mod.__version__)
            if info:
                self.notify(
                    f"[b]{info.tag}[/b] is available — open Settings to install.",
                    title="Update Available",
                    timeout=12,
                )
        except Exception:
            pass  # silently ignore network errors on background check

    def set_connection_status(self, *, connected: bool, host: str = "") -> None:
        self.connection_status = {
            "connected": connected,
            "text": host if connected else "Not connected",
        }
        # Update any visible ConnectionBar widgets
        from app.tui.widgets.connection_bar import ConnectionBar
        for bar in self.query(ConnectionBar):
            bar.set_status(connected=connected, text=self.connection_status["text"])
