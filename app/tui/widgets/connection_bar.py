"""Top connection status bar."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class ConnectionBar(Widget):
    DEFAULT_CSS = ""
    status_text: reactive[str] = reactive("Not connected")
    connected: reactive[bool] = reactive(False)

    def render(self) -> str:
        icon = "[green]●[/]" if self.connected else "[red]●[/]"
        return f"{icon} FTP: {self.status_text}"

    def set_status(self, *, connected: bool, text: str) -> None:
        self.connected = connected
        self.status_text = text
