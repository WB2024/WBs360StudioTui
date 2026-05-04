from __future__ import annotations

import app as app_mod
import asyncio
import shutil
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static, Switch

from app.config.settings import cache_dir, save_settings
from app.core.ftp_client import FtpClient
from app.tui.screens.connection import ConnectionScreen


class UpdateConfirmModal(ModalScreen[bool]):
    """Ask the user to confirm downloading and applying an update."""

    DEFAULT_CSS = """
    UpdateConfirmModal { align: center middle; }
    #uc_box {
        width: 60; height: auto;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    #uc_box Button { width: 100%; margin-top: 1; }
    #uc_notes { max-height: 8; overflow-y: auto; color: $text-muted; }
    """

    def __init__(self, tag: str, is_pre: bool, notes: str) -> None:
        super().__init__()
        self._tag = tag
        self._is_pre = is_pre
        self._notes = notes

    def compose(self) -> ComposeResult:
        pre_label = " [dim](pre-release)[/]" if self._is_pre else ""
        with Vertical(id="uc_box"):
            yield Static(f"[b]Update available: {self._tag}{pre_label}[/b]")
            if self._notes.strip():
                yield Static(self._notes.strip()[:400], id="uc_notes")
            yield Static("\nDownload and install now?")
            yield Button("Update Now", id="uc_yes", variant="success")
            yield Button("Later", id="uc_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "uc_yes")


class SettingsScreen(Screen):
    TITLE = "Settings"
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            # ── Updates ──────────────────────────────────────────────────────
            yield Static("[b cyan]Updates[/]")
            yield Static(f"[dim]Current version: [bold]{app_mod.__version__}[/bold][/]")
            yield Static("[dim]Check GitHub Releases for newer versions of x360tm.[/]")
            with Horizontal():
                yield Label("Auto-update on launch  ")
                yield Switch(value=self.app.settings.auto_update, id="auto_update")
            yield Static("[dim]Channel[/]")
            yield Select(
                options=[("Latest stable", "latest"), ("Pre-release", "pre-release")],
                value=self.app.settings.update_channel,
                id="update_channel",
            )
            with Horizontal():
                yield Button("Check for Updates", id="check_updates", variant="primary")
                yield Static("", id="update_status")

            # ── Connection Profiles ───────────────────────────────────────────
            yield Static("\n[b cyan]Connection Profiles[/]")
            yield ListView(id="profiles_list")
            with Horizontal():
                yield Button("Add", id="add_profile", variant="primary")
                yield Button("Edit", id="edit_profile")
                yield Button("Delete", id="del_profile", variant="error")
                yield Button("Set Default", id="default_profile")

            # ── FTP Connection ────────────────────────────────────────────────
            yield Static("\n[b cyan]FTP Connection[/]")
            yield Static("", id="ftp_status", classes="muted")
            with Horizontal():
                yield Button("Test Connection", id="ftp_test", variant="primary")
                yield Button("Reconnect", id="ftp_reconnect", variant="success")
                yield Button("Disconnect", id="ftp_disconnect")

            # ── Cache ─────────────────────────────────────────────────────────
            yield Static("\n[b cyan]Cache[/]")
            yield Static("", id="cache_info")
            with Horizontal():
                yield Button("Refresh DB", id="refresh_db", variant="primary")
                yield Button("Clear Cache", id="clear_cache", variant="error")

            # ── qBittorrent ───────────────────────────────────────────────────
            yield Static("\n[b cyan]qBittorrent Connection[/]")
            yield Static("[dim]Host, port, and credentials for the qBittorrent Web UI.[/]")
            yield Input(value=self.app.settings.qbit_host, placeholder="localhost", id="qbit_host")
            yield Input(value=str(self.app.settings.qbit_port), placeholder="8080", id="qbit_port")
            yield Input(value=self.app.settings.qbit_username, placeholder="admin", id="qbit_username")
            yield Input(
                value=self.app.settings.qbit_password,
                placeholder="adminadmin",
                id="qbit_password",
                password=True,
            )

            # ── USB Mount (Manual) ────────────────────────────────────────────
            yield Static("\n[b cyan]USB Mount (Manual)[/]")
            yield Input(
                value=self.app.settings.usb.manual_path or "",
                placeholder="Manual USB mount path (leave empty for auto-detect)",
                id="usb_path",
            )

            # ── Local Paths ───────────────────────────────────────────────────
            yield Static("\n[b cyan]Local Paths[/]")

            yield Static("\n[b]Local Downloads[/]")
            yield Static("[dim]Destination for downloaded mods, homebrew, saves and other content.[/]")
            yield Input(value=self.app.settings.download_dir, id="dl_dir")

            yield Static("\n[b]Local Mods Folder[/]")
            yield Static("[dim]Folder containing local mod files. Leave empty for default (LocalMods/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_mods_path,
                placeholder="e.g. /srv/Xbox360/Mods",
                id="local_mods_path",
            )

            yield Static("\n[b]Local Trainers Folder[/]")
            yield Static("[dim]Folder containing local trainer files. Leave empty for default (LocalTrainers/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_trainers_path,
                placeholder="e.g. /home/user/Desktop/TrainerFiles",
                id="local_trainers_path",
            )

            yield Static("\n[b]Local Homebrew Folder[/]")
            yield Static("[dim]Folder containing local homebrew apps. Leave empty for default (LocalHomebrew/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_homebrew_path,
                placeholder="e.g. /home/user/Xbox360/Homebrew",
                id="local_homebrew_path",
            )

            yield Static("\n[b]Local Game Saves Folder[/]")
            yield Static("[dim]Folder containing local game save files. Leave empty for default (LocalGameSaves/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_game_saves_path,
                placeholder="e.g. /home/user/Xbox360/GameSaves",
                id="local_game_saves_path",
            )

            yield Static("\n[b]Local Patches Folder[/]")
            yield Static("[dim]Folder containing local patch TOML files. Leave empty for default (LocalPatches/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_patches_path,
                placeholder="e.g. /home/user/Xbox360/Patches",
                id="local_patches_path",
            )

            yield Static("\n[b]Local Cheats Folder[/]")
            yield Static("[dim]Folder containing local cheat files. Leave empty for default (LocalCheats/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_cheats_path,
                placeholder="e.g. /home/user/Xbox360/Cheats",
                id="local_cheats_path",
            )

            yield Static("\n[b]Local Title Updates Folder[/]")
            yield Static("[dim]Folder containing local STFS Title Update packages. Leave empty for default (LocalTitleUpdates/ in app directory).[/]")
            yield Input(
                value=self.app.settings.local_title_updates_path,
                placeholder="e.g. /home/user/Xbox360/TitleUpdates",
                id="local_title_updates_path",
            )

            yield Static("\n[b]Local ISO Path[/]")
            yield Static("[dim]Local folder containing Xbox 360 ISO files.\nSupports flat (GameName.iso) and subfoldered ({GameName}/{GameName}.iso) layouts.[/]")
            yield Input(
                value=self.app.settings.local_iso_path,
                placeholder="e.g. /home/user/Xbox360/ISOs",
                id="local_iso_path",
            )

            yield Static("\n[b]Local GOD Path[/]")
            yield Static("[dim]Local folder containing GOD (Games on Demand) format games.\nExpected structure: {GameName}/{TitleID}/{ContentType}/{ContainerFile}[/]")
            yield Input(
                value=self.app.settings.local_god_path,
                placeholder="e.g. /home/user/Xbox360/GODs",
                id="local_god_path",
            )

            yield Static("\n[b]Torrent Watch Folder[/]")
            yield Static(
                "[dim]Local folder where qBittorrent will save downloaded torrents.\n"
                "Used for legally-owned Xbox 360 game backup torrents only.[/]"
            )
            yield Input(
                value=self.app.settings.torrent_download_folder,
                placeholder="e.g. /home/user/Xbox360/Torrents",
                id="torrent_download_folder",
            )

            yield Static("\n[b]Torrent Picker Folder[/]")
            yield Static("[dim].torrent files to browse in the Torrent Picker screen.\nLeave empty to use the bundled Torrent/ folder in the app directory.[/]")
            yield Input(
                value=self.app.settings.torrent_folder,
                placeholder="Leave empty for default (Torrent/ in app directory)",
                id="torrent_folder",
            )

            yield Static("\n[b]USB Backup Directory[/]")
            yield Static("[dim]Local folder to store USB backup images.\nLeave empty to use default (USBBackups/ in app folder).[/]")
            yield Input(
                value=self.app.settings.backup_dir,
                placeholder="Leave empty for default",
                id="backup_dir",
            )

            # ── Console Paths ─────────────────────────────────────────────────
            yield Static("\n[b cyan]Console Paths[/]")

            yield Static("\n[b]Aurora Folder Path[/]")
            yield Static("[dim]Used to resolve {AURORAPATH} in trainer install paths.[/]")
            yield Input(
                value=self.app.settings.aurora_path,
                placeholder="e.g. Hdd:\\Aurora\\",
                id="aurora_path",
            )

            yield Static("\n[b]Game Library Paths[/]")
            yield Static(
                "[dim]Xbox game folder paths to scan for Title ID subfolders.\n"
                "Separate multiple paths with a semicolon.\n"
                "e.g. Usb1:\\Games;Usb0:\\Games[/]"
            )
            yield Input(
                value=";".join(self.app.settings.game_paths),
                placeholder="e.g. Usb1:\\Games",
                id="game_paths",
            )

            yield Static("\n[b]Library Scan Depth[/]")
            yield Static("[dim]Max folder levels to traverse when scanning for Title IDs.\nSet to 4 if games sit inside a friendly parent folder (Games/Minecraft/4D530A81).[/]")
            yield Input(
                value=str(self.app.settings.game_scan_depth),
                placeholder="4",
                id="game_scan_depth",
            )

            yield Static("\n[b]Game Install Destination[/]")
            yield Static("[dim]Xbox path where GOD games will be transferred to.\ne.g. Hdd:\\Content\\0000000000000000\\ or Usb0:\\Games[/]")
            yield Input(
                value=self.app.settings.game_install_path,
                placeholder="e.g. Hdd:\\Content\\0000000000000000\\",
                id="game_install_path",
            )

            # ── Console Install Paths ─────────────────────────────────────────
            yield Static("\n[b cyan]Console Install Paths[/]")
            yield Static("[dim]Override where each content type is installed on the console.\nLeave empty to use the standard paths from the Arisen Studio database.[/]")

            yield Static("\n[b]Mods Install Path[/]")
            yield Static("[dim]Xbox path for mod installs. e.g. Hdd:\\JTAG\\[/]")
            yield Input(
                value=self.app.settings.mod_install_path,
                placeholder="e.g. Hdd:\\JTAG\\ (leave empty for DB default)",
                id="mod_install_path",
            )

            yield Static("\n[b]Trainers Install Path[/]")
            yield Static("[dim]Xbox base path for trainer installs. {AURORAPATH} is appended when empty.\ne.g. Hdd:\\Aurora\\User\\Trainers\\[/]")
            yield Input(
                value=self.app.settings.trainer_install_path,
                placeholder="Leave empty to use {AURORAPATH}\\User\\Trainers\\",
                id="trainer_install_path",
            )

            yield Static("\n[b]Homebrew Install Path[/]")
            yield Static("[dim]Xbox base path for homebrew app installs.\ne.g. Hdd:\\Content\\0000000000000000\\[/]")
            yield Input(
                value=self.app.settings.homebrew_install_path,
                placeholder="Leave empty for DB default",
                id="homebrew_install_path",
            )

            yield Static("\n[b]Game Saves Install Path[/]")
            yield Static("[dim]Xbox base path for game save installs.\ne.g. Hdd:\\Content\\0000000000000000\\[/]")
            yield Input(
                value=self.app.settings.game_save_install_path,
                placeholder="Leave empty for DB default",
                id="game_save_install_path",
            )

            yield Static("\n[b]Title Update Install Path[/]")
            yield Static("[dim]Xbox base path for Title Update installs (FTP). Drive letter is auto-derived from Game Install Destination.\ne.g. Usb1:\\Content\\0000000000000000\\[/]")
            yield Input(
                value=self.app.settings.title_update_install_path,
                placeholder="Leave empty for standard path",
                id="title_update_install_path",
            )

            # ── Save / Back ───────────────────────────────────────────────────
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
            app.settings.local_iso_path = self.query_one("#local_iso_path", Input).value.strip()
            app.settings.torrent_download_folder = self.query_one("#torrent_download_folder", Input).value.strip()
            app.settings.qbit_host = self.query_one("#qbit_host", Input).value.strip() or "localhost"
            try:
                app.settings.qbit_port = int(self.query_one("#qbit_port", Input).value or "8080")
            except ValueError:
                app.settings.qbit_port = 8080
            app.settings.qbit_username = self.query_one("#qbit_username", Input).value.strip() or "admin"
            app.settings.qbit_password = self.query_one("#qbit_password", Input).value or "adminadmin"
            app.settings.backup_dir = self.query_one("#backup_dir", Input).value.strip()
            app.settings.local_mods_path = self.query_one("#local_mods_path", Input).value.strip()
            app.settings.local_trainers_path = self.query_one("#local_trainers_path", Input).value.strip()
            app.settings.local_homebrew_path = self.query_one("#local_homebrew_path", Input).value.strip()
            app.settings.local_game_saves_path = self.query_one("#local_game_saves_path", Input).value.strip()
            app.settings.local_patches_path = self.query_one("#local_patches_path", Input).value.strip()
            app.settings.local_cheats_path = self.query_one("#local_cheats_path", Input).value.strip()
            app.settings.local_title_updates_path = self.query_one("#local_title_updates_path", Input).value.strip()
            app.settings.torrent_folder = self.query_one("#torrent_folder", Input).value.strip()
            app.settings.mod_install_path = self.query_one("#mod_install_path", Input).value.strip()
            app.settings.trainer_install_path = self.query_one("#trainer_install_path", Input).value.strip()
            app.settings.homebrew_install_path = self.query_one("#homebrew_install_path", Input).value.strip()
            app.settings.game_save_install_path = self.query_one("#game_save_install_path", Input).value.strip()
            app.settings.title_update_install_path = self.query_one("#title_update_install_path", Input).value.strip()
            app.settings.auto_update = self.query_one("#auto_update", Switch).value
            sel = self.query_one("#update_channel", Select)
            if sel.value and sel.value is not Select.BLANK:
                app.settings.update_channel = str(sel.value)
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
        elif bid == "check_updates":
            self.run_worker(self._check_updates_worker(), exclusive=True)

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
            await asyncio.to_thread(self.app.db.load_all, self.app.settings)
            self.app.settings.mark_db_fetched()
            save_settings(self.app.settings)
            self._refresh_cache_info()
        except Exception:
            pass

    async def _check_updates_worker(self) -> None:
        from app.core.updater import UpdateInfo, check_for_update, download_update, apply_update, restart_app

        status = self.query_one("#update_status", Static)
        status.update("[yellow]Checking…[/]")
        try:
            channel = self.app.settings.update_channel
            info: UpdateInfo | None = await check_for_update(channel, app_mod.__version__)
        except Exception as e:
            status.update(f"[red]Check failed: {e}[/]")
            return

        if info is None:
            status.update("[green]You're up to date![/]")
            return

        # Source runs can't auto-install — just report
        if not getattr(sys, "frozen", False):
            status.update(f"[yellow]{info.tag} available — run 'git pull' to update[/]")
            return

        status.update(f"[yellow]{info.tag} available[/]")
        confirmed = await self.app.push_screen_wait(
            UpdateConfirmModal(info.tag, info.is_prerelease, info.body)
        )
        if not confirmed:
            status.update("")
            return

        await self._do_update(info, status)

    async def _do_update(self, info, status: Static) -> None:
        from app.core.updater import download_update, apply_update, restart_app

        def _progress(received: int, total: int) -> None:
            if total:
                mb_recv = received // (1024 * 1024)
                mb_tot = total // (1024 * 1024)
                status.update(f"[yellow]Downloading… {mb_recv} / {mb_tot} MB[/]")
            else:
                status.update(f"[yellow]Downloading… {received // (1024 * 1024)} MB[/]")

        try:
            archive = await download_update(info, cache_dir(), on_progress=_progress)
        except Exception as e:
            status.update(f"[red]Download failed: {e}[/]")
            return

        status.update("[yellow]Applying update…[/]")
        try:
            should_restart = apply_update(archive)
        except Exception as e:
            status.update(f"[red]Update failed: {e}[/]")
            return

        if should_restart:
            # Linux — binary replaced in-place; re-exec after Textual exits
            status.update("[green]Update applied! Restarting…[/]")
            await asyncio.sleep(1)
            self.app.exit(result="restart")
        else:
            # Windows — PS1 helper will replace binary and relaunch
            status.update("[green]Update downloaded! App will restart shortly.[/]")
            await asyncio.sleep(2)
            self.app.exit()

    def action_back(self) -> None:
        self.app.pop_screen()
