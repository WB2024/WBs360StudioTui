"""Splash screen — fetch DBs on startup."""
from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from app.config.settings import save_settings

LOGO = r"""
 __  __  ____  ____   ___ _____ __  __ 
 \ \/ / |___ \|___ \ / _ \_   _|  \/  |
  \  /    __) | __) | | | || | | |\/| |
  /  \   / __/ / __/| |_| || | | |  | |
 /_/\_\ |_____|_____|\___/ |_| |_|  |_|

       Xbox 360 Mod Manager TUI
"""


class SplashScreen(Screen):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(LOGO, id="splash_logo")
            yield Static("Loading...", id="splash_log")

    async def on_mount(self) -> None:
        self.run_worker(self._boot(), exclusive=True)

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#splash_log", Static).update(msg)
        except Exception:
            pass

    async def _boot(self) -> None:
        app = self.app  # type: ignore[assignment]
        db = app.db
        settings = app.settings

        self._log("Checking server...")
        online = await db.check_status()

        cache_age = db.cache_age_hours()
        need_fetch = online and (cache_age is None or cache_age > settings.db_cache_max_age_hours)

        if need_fetch:
            try:
                await db.fetch_all(progress=lambda m: self._log(m))
                settings.mark_db_fetched()
                save_settings(settings)
            except Exception as e:
                self._log(f"[red]Fetch failed:[/red] {e}. Trying cache...")
                await asyncio.sleep(1.5)

        if not db.has_cache():
            self._log("[red]No cached data and unable to fetch. Cannot continue.[/red]")
            await asyncio.sleep(3)
            app.exit()
            return

        self._log("Loading databases...")
        await asyncio.to_thread(db.load_all, app.settings)

        if not online:
            self._log(f"[yellow]Offline — using cached data[/yellow]")
            await asyncio.sleep(1)

        # Switch to main menu
        from app.tui.screens.main_menu import MainMenuScreen
        await app.push_screen(MainMenuScreen())
        # Replace splash by popping it underneath — main menu now top
