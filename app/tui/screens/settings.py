from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from app.config.settings import cache_dir, save_settings
from app.tui.screens.connection import ConnectionScreen


class SettingsScreen(Screen):
    TITLE = "Settings"
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            yield Static("[b cyan]Connection Profiles[/]")
            yield ListView(id="profiles_list")
            with Horizontal():
                yield Button("Add", id="add_profile", variant="primary")
                yield Button("Edit", id="edit_profile")
                yield Button("Delete", id="del_profile", variant="error")
                yield Button("Set Default", id="default_profile")
            yield Static("\n[b cyan]Download Directory[/]")
            yield Input(value=self.app.settings.download_dir, id="dl_dir")
            yield Static("\n[b cyan]Cache[/]")
            yield Static("", id="cache_info")
            with Horizontal():
                yield Button("Refresh DB", id="refresh_db", variant="primary")
                yield Button("Clear Cache", id="clear_cache", variant="error")
            yield Static("\n[b cyan]USB[/]")
            yield Input(
                value=self.app.settings.usb.manual_path or "",
                placeholder="Manual USB mount (leave empty for auto-detect)",
                id="usb_path",
            )
            with Horizontal():
                yield Button("Save", id="save_settings", variant="success")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_profiles()
        self._refresh_cache_info()

    def _refresh_profiles(self) -> None:
        lv = self.query_one("#profiles_list", ListView)
        lv.clear()
        for p in self.app.settings.connections:
            tag = " [green](default)[/]" if p.is_default else ""
            item = ListItem(Label(f"{p.name} — {p.host}:{p.port} ({p.username}){tag}"))
            item.data = p.id  # type: ignore[attr-defined]
            lv.append(item)

    def _refresh_cache_info(self) -> None:
        age = self.app.db.cache_age_hours()
        if age is None:
            text = "No cache."
        else:
            text = f"Cache age: {age:.1f} hours  |  Path: {cache_dir()}"
        self.query_one("#cache_info", Static).update(text)

    def _selected_profile_id(self) -> str | None:
        lv = self.query_one("#profiles_list", ListView)
        if lv.highlighted_child and hasattr(lv.highlighted_child, "data"):
            return lv.highlighted_child.data  # type: ignore[attr-defined]
        return None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        app = self.app
        if bid == "back":
            self.app.pop_screen()
        elif bid == "add_profile":
            prof = await app.push_screen_wait(ConnectionScreen())
            if prof:
                self._refresh_profiles()
        elif bid == "edit_profile":
            pid = self._selected_profile_id()
            if pid:
                target = next((c for c in app.settings.connections if c.id == pid), None)
                if target:
                    await app.push_screen_wait(ConnectionScreen(target))
                    self._refresh_profiles()
        elif bid == "del_profile":
            pid = self._selected_profile_id()
            if pid:
                app.settings.delete_profile(pid)
                save_settings(app.settings)
                self._refresh_profiles()
        elif bid == "default_profile":
            pid = self._selected_profile_id()
            if pid:
                for c in app.settings.connections:
                    c.is_default = (c.id == pid)
                save_settings(app.settings)
                self._refresh_profiles()
        elif bid == "save_settings":
            app.settings.download_dir = self.query_one("#dl_dir", Input).value
            app.settings.usb.manual_path = self.query_one("#usb_path", Input).value or None
            save_settings(app.settings)
        elif bid == "refresh_db":
            self.run_worker(self._refresh_db_worker(), exclusive=True)
        elif bid == "clear_cache":
            try:
                for f in cache_dir().glob("*"):
                    if f.is_file():
                        f.unlink()
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                self._refresh_cache_info()
            except Exception:
                pass

    async def _refresh_db_worker(self) -> None:
        try:
            await self.app.db.fetch_all()
            await asyncio.to_thread(self.app.db.load_all)
            self.app.settings.mark_db_fetched()
            save_settings(self.app.settings)
            self._refresh_cache_info()
        except Exception:
            pass

    def action_back(self) -> None:
        self.app.pop_screen()
