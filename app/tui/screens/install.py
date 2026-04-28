"""Install choice + USB drive selection modals + install runner."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from app.config.settings import save_settings
from app.core.ftp_client import FtpClient
from app.core.installer import Installer, InstallResult
from app.core.usb_manager import UsbDrive, UsbManager
from app.tui.screens.connection import ConnectionScreen
from app.tui.widgets.progress_modal import ProgressModal


class InstallChoiceModal(ModalScreen[Optional[str]]):
    """Returns 'ftp' | 'usb' | 'download' | None."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]Install Method[/]")
            yield Button("FTP — to Xbox 360", id="ic_ftp", variant="primary")
            yield Button("USB — to mounted drive", id="ic_usb")
            yield Button("Download Only", id="ic_dl")
            yield Button("Cancel", id="ic_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        m = {"ic_ftp": "ftp", "ic_usb": "usb", "ic_dl": "download", "ic_cancel": None}
        self.dismiss(m.get(event.button.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class UsbChoiceModal(ModalScreen[Optional[str]]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]Choose USB Drive[/]")
            yield ListView(id="usb_list")
            with Horizontal():
                yield Button("Use Selected", id="usb_ok", variant="primary")
                yield Button("Cancel", id="usb_cancel")

    def on_mount(self) -> None:
        usb = UsbManager()
        drives = usb.detect_drives()
        lv = self.query_one("#usb_list", ListView)
        if not drives:
            lv.append(ListItem(Label("[yellow]No removable drives detected[/]")))
            return
        for d in drives:
            item = ListItem(Label(d.display))
            item.data = d.mount_point  # type: ignore[attr-defined]
            lv.append(item)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "usb_cancel":
            self.dismiss(None)
            return
        lv = self.query_one("#usb_list", ListView)
        if lv.highlighted_child and hasattr(lv.highlighted_child, "data"):
            self.dismiss(lv.highlighted_child.data)  # type: ignore[attr-defined]
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


async def run_install_flow(app, item: Any) -> None:
    """Orchestrate install for any installable item from a browser screen."""
    method = await app.push_screen_wait(InstallChoiceModal())
    if not method:
        return

    installer: Installer = app.installer

    if method == "download":
        modal = ProgressModal(f"Downloading: {getattr(item, 'name', 'item')}")
        await app.push_screen(modal)

        def cb(stage: str, cur: int, total: int) -> None:
            modal.set_stage(f"Downloading...", cur, total)

        try:
            paths = await installer.download_only(item, app.settings.download_dir, progress=cb)
            modal.set_done(f"Saved {len(paths)} file(s) to {app.settings.download_dir}", success=True)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)
        return

    if method == "ftp":
        prof = app.settings.default_profile()
        if prof is None:
            prof = await app.push_screen_wait(ConnectionScreen())
            if not prof:
                return

        modal = ProgressModal(f"Installing via FTP: {getattr(item, 'name', 'item')}")
        await app.push_screen(modal)
        client = FtpClient(prof.host, prof.port, prof.username, prof.password)

        def cb(stage: str, cur: int, total: int) -> None:
            label = {"download": "Downloading", "extract": "Extracting", "transfer": "Transferring"}.get(stage, stage)
            modal.set_stage(f"{label}...", cur, total)

        try:
            await client.connect()
            app.set_connection_status(connected=True, host=f"{prof.host}:{prof.port}")
            result: InstallResult = await installer.install_via_ftp(item, client, progress=cb)
            await client.disconnect()
            modal.set_done(result.message, success=result.success)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)
            try:
                await client.disconnect()
            except Exception:
                pass
        return

    if method == "usb":
        usb_root = await app.push_screen_wait(UsbChoiceModal())
        if not usb_root:
            return
        modal = ProgressModal(f"Installing to USB: {getattr(item, 'name', 'item')}")
        await app.push_screen(modal)

        def cb(stage: str, cur: int, total: int) -> None:
            label = {"download": "Downloading", "extract": "Extracting", "transfer": "Copying"}.get(stage, stage)
            modal.set_stage(f"{label}...", cur, total)

        try:
            result = await installer.install_via_usb(item, usb_root, progress=cb)
            modal.set_done(result.message, success=result.success)
        except Exception as e:
            modal.set_done(f"Failed: {e}", success=False)
