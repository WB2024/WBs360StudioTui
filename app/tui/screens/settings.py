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
from app.core.ftp_client import FtpClient
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
            yield Static("\n[b cyan]FTP Connection[/]")
            yield Static("", id="ftp_status", classes="muted")
            with Horizontal():
                yield Button("Test Connection", id="ftp_test", variant="primary")
                yield Button("Reconnect", id="ftp_reconnect", variant="success")
                yield Button("Disconnect", id="ftp_disconnect")
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
            yield Static("\n[b cyan]Aurora Folder Path[/]")
            yield Static("[dim]Used to resolve {AURORAPATH} in trainer install paths[/]")
            yield Input(
                value=self.app.settings.aurora_path,
                placeholder="e.g. Hdd:\\Aurora\\",
                id="aurora_path",
            )
            yield Static("\n[b cyan]Game Library Paths[/]")
            yield Static(
                "[dim]Xbox game folder paths to scan for Title ID subfolders.\n"
                "Separate multiple paths with a semicolon.\n"
                "e.g. Usb1\\Games;Usb0\\Games[/]"
            )
            yield Input(
                value=";".join(self.app.settings.game_paths),
                placeholder="e.g. Usb1\\Games",
                id="game_paths",
            )
            yield Static("\n[b cyan]Library Scan Depth[/]")
            yield Static("[dim]Max folder levels to traverse when scanning for Title IDs.\nSet to 4 if you use a friendly parent folder (Games/Minecraft/4D530A81).[/]")
            yield Input(
                value=str(self.app.settings.game_scan_depth),
                placeholder="4",
                id="game_scan_depth",
            )
            yield Static("\n[b cyan]Local GOD Path[/]")
            yield Static("[dim]Local PC folder containing GOD (Games on Demand) format games.\nExpected structure: {GameName}/{TitleID}/{ContentType}/{ContainerFile}[/]")
            yield Input(
                value=self.app.settings.local_god_path,
                placeholder="e.g. D:\\Xbox360\\GODs",
                id="local_god_path",
            )
            yield Static("\n[b cyan]Game Install Destination[/]")
            yield Static("[dim]Xbox path where GOD games will be transferred to.\ne.g. Hdd:\\Content\\0000000000000000\\ or Usb0\\Games[/]")
            yield Input(
                value=self.app.settings.game_install_path,
                placeholder="e.g. Hdd:\\Content\\0000000000000000\\",
                id="game_install_path",
            )
            with Horizontal():
                yield Button("Save", id="save_settings", variant="success")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_profiles()
        self._refresh_cache_info()
        self._refresh_ftp_status()

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

    def _refresh_ftp_status(self) -> None:
        s = self.app.connection_status
        if s.get("connected"):
            self.query_one("#ftp_status", Static).update(f"[green]Connected — {s['text']}[/]")
        else:
            self.query_one("#ftp_status", Static).update("[dim]Not connected[/]")

    def _set_ftp_status(self, text: str, ok: bool | None = None) -> None:
        colour = "green" if ok is True else "red" if ok is False else "white"
        self.query_one("#ftp_status", Static).update(f"[{colour}]{text}[/]")

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
        elif bid == "ftp_test":
            prof = app.settings.default_profile()
            if prof is None:
                self._set_ftp_status("No default profile configured.", ok=False)
            else:
                self._set_ftp_status("Testing…")
                self.run_worker(self._ftp_test_worker(prof), exclusive=False)
        elif bid == "ftp_reconnect":
            prof = app.settings.default_profile()
            if prof is None:
                self._set_ftp_status("No default profile configured.", ok=False)
            else:
                self._set_ftp_status("Connecting…")
                self.run_worker(self._ftp_reconnect_worker(prof), exclusive=False)
        elif bid == "ftp_disconnect":
            app.set_connection_status(connected=False)
            self._refresh_ftp_status()
        elif bid == "save_settings":
            app.settings.download_dir = self.query_one("#dl_dir", Input).value
            app.settings.usb.manual_path = self.query_one("#usb_path", Input).value or None
            app.settings.aurora_path = self.query_one("#aurora_path", Input).value or "Hdd:\\Aurora\\"
            raw_paths = self.query_one("#game_paths", Input).value
            app.settings.game_paths = [p.strip() for p in raw_paths.split(";") if p.strip()]
            try:
                app.settings.game_scan_depth = max(1, int(self.query_one("#game_scan_depth", Input).value or "4"))
            except ValueError:
                app.settings.game_scan_depth = 4
            app.settings.local_god_path = self.query_one("#local_god_path", Input).value.strip()
            app.settings.game_install_path = self.query_one("#game_install_path", Input).value.strip() or "Hdd:\\Content\\0000000000000000\\"
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

    async def _ftp_test_worker(self, prof) -> None:
        client = FtpClient(prof.host, prof.port, prof.username, prof.password)
        try:
            await client.connect()
            await client.disconnect()
            self.app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")
            self._set_ftp_status(f"Connection OK — {prof.host}:{prof.port}", True)
        except Exception as e:
            msg = str(e)
            reason = msg.split(": ", 2)[-1] if ": " in msg else msg
            self.app.set_connection_status(connected=False)
            self._set_ftp_status(f"Failed: {reason}", False)

    async def _ftp_reconnect_worker(self, prof) -> None:
        client = FtpClient(prof.host, prof.port, prof.username, prof.password)
        try:
            await client.connect()
            await client.disconnect()
            self.app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")
            self._set_ftp_status(f"Connected — {prof.host}:{prof.port}", True)
        except Exception as e:
            msg = str(e)
            reason = msg.split(": ", 2)[-1] if ": " in msg else msg
            self.app.set_connection_status(connected=False)
            self._set_ftp_status(f"Reconnect failed: {reason}", False)

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
