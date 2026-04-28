"""Bottom status bar."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class StatusBar(Widget):
    text: reactive[str] = reactive("Ready")

    def render(self) -> str:
        return self.text

    def set_text(self, t: str) -> None:
        self.text = t
