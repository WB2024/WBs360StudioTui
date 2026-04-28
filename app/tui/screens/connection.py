"""FTP connection profile screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from app.config.settings import save_settings
from app.core.ftp_client import FtpClient
from app.models.connection import ConnectionProfile


class ConnectionScreen(ModalScreen[ConnectionProfile | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, profile: ConnectionProfile | None = None) -> None:
        super().__init__()
        self.profile = profile or ConnectionProfile()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b cyan]FTP Connection[/]", id="conn_title")
            yield Label("Profile name:")
            yield Input(value=self.profile.name, id="conn_name")
            yield Label("Host / IP:")
            yield Input(value=self.profile.host, placeholder="192.168.1.100", id="conn_host")
            yield Label("Port:")
            yield Input(value=str(self.profile.port), id="conn_port")
            yield Label("Username:")
            yield Input(value=self.profile.username, id="conn_user")
            yield Label("Password:")
            yield Input(value=self.profile.password, password=True, id="conn_pass")
            yield Static("", id="conn_status", classes="muted")
            with Horizontal():
                yield Button("Test", id="conn_test")
                yield Button("Save", id="conn_save", variant="primary")
                yield Button("Connect", id="conn_connect", variant="success")
                yield Button("Cancel", id="conn_cancel")

    def _read(self) -> ConnectionProfile:
        try:
            port = int(self.query_one("#conn_port", Input).value or "21")
        except ValueError:
            port = 21
        self.profile.name = self.query_one("#conn_name", Input).value or "My Xbox 360"
        self.profile.host = self.query_one("#conn_host", Input).value
        self.profile.port = port
        self.profile.username = self.query_one("#conn_user", Input).value or "xbox"
        self.profile.password = self.query_one("#conn_pass", Input).value
        return self.profile

    def _set_status(self, text: str, ok: bool | None = None) -> None:
        css = "success" if ok else "error" if ok is False else "muted"
        self.query_one("#conn_status", Static).update(f"[{ 'green' if ok else 'red' if ok is False else 'white'}]{text}[/]")
        self.query_one("#conn_status", Static).set_classes(css)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "conn_cancel":
            self.dismiss(None)
            return
        prof = self._read()
        if bid == "conn_test":
            self._set_status("Testing...")
            client = FtpClient(prof.host, prof.port, prof.username, prof.password)
            ok = False
            try:
                ok = await client.test_connection()
            except Exception as e:
                self._set_status(f"Error: {e}", ok=False)
                return
            self._set_status("Connection OK" if ok else "Connection failed", ok=ok)
        elif bid == "conn_save":
            app = self.app  # type: ignore[assignment]
            if not prof.host:
                self._set_status("Host required", ok=False)
                return
            app.settings.update_profile(prof)
            save_settings(app.settings)
            self._set_status("Saved", ok=True)
        elif bid == "conn_connect":
            if not prof.host:
                self._set_status("Host required", ok=False)
                return
            app = self.app  # type: ignore[assignment]
            app.settings.update_profile(prof)
            save_settings(app.settings)
            self.dismiss(prof)

    def action_cancel(self) -> None:
        self.dismiss(None)
