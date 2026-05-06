"""Progress modal screen for installs/downloads."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, ProgressBar, RichLog, Static


class ProgressModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, title: str = "Working...") -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="pm_title")
            yield Static("Initializing...", id="pm_stage")
            yield ProgressBar(total=100, show_eta=False, id="pm_bar")
            yield RichLog(id="pm_log", max_lines=500, markup=True, highlight=False)
            yield Button("Close", id="pm_close", variant="primary")

    def set_stage(self, stage: str, current: int, total: int) -> None:
        try:
            self.query_one("#pm_stage", Static).update(stage)
            bar = self.query_one("#pm_bar", ProgressBar)
            if total > 0:
                bar.update(total=total, progress=current)
            else:
                # No known total — show indeterminate pulsing animation
                bar.update(total=None)
        except Exception:
            pass

    def set_detail(self, text: str) -> None:
        """Append a line to the scrollable log box."""
        try:
            self.query_one("#pm_log", RichLog).write(text)
        except Exception:
            pass

    def set_done(self, message: str, success: bool = True) -> None:
        try:
            self.query_one("#pm_stage", Static).update(
                f"[green]✓ Complete[/]" if success else f"[red]✗ Failed[/]"
            )
            bar = self.query_one("#pm_bar", ProgressBar)
            if success:
                bar.update(total=1, progress=1)
            else:
                bar.update(total=None)
            self.query_one("#pm_log", RichLog).write(
                f"[green]{message}[/]" if success else f"[red]{message}[/]"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pm_close":
            self.dismiss()
