"""TU Compatibility Checker screen.

Reads the STFS headers of a game file and a Title Update file and reports
whether the Title Update is compatible with the game (Title ID + Media ID
must both match).
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from app.core.tu_scanner import StfsInfo, read_stfs_info
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.status_bar import StatusBar


class TuCheckerScreen(Screen):
    """Check whether a Title Update is compatible with a game file."""

    TITLE = "TU Compatibility Checker"
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+enter", "check", "Check", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")
        with VerticalScroll(id="tu_scroll"):
            yield Static("[b cyan]TU Compatibility Checker[/b cyan]", id="tu_title")
            yield Static(
                "[dim]Compare a game file and a Title Update package to check whether "
                "the TU is compatible (Title ID and Media ID must both match).[/dim]",
                id="tu_desc",
            )

            # ── Game file ────────────────────────────────────────────────
            yield Static("\n[b]Game File[/b]")
            yield Static(
                "[dim]Path to the game's STFS package (e.g. the default.xex container "
                "or the content package inside the game folder).[/dim]",
            )
            with Horizontal(id="game_row"):
                yield Input(
                    placeholder="e.g. /path/to/game or drag-and-drop path here",
                    id="game_path",
                )
                yield Button("Clear", id="clear_game", classes="clear_btn")

            # ── TU file ──────────────────────────────────────────────────
            yield Static("\n[b]Title Update File[/b]")
            yield Static(
                "[dim]Path to the Title Update STFS package "
                "(typically found in LocalTitleUpdates/).[/dim]",
            )
            with Horizontal(id="tu_row"):
                yield Input(
                    placeholder="e.g. /path/to/title_update",
                    id="tu_path",
                )
                yield Button("Clear", id="clear_tu", classes="clear_btn")

            # ── Actions ──────────────────────────────────────────────────
            with Horizontal(id="tu_actions"):
                yield Button("Check Compatibility", id="btn_check", variant="primary")
                yield Button("Reset", id="btn_reset")
                yield Button("Back [Esc]", id="btn_back")

            # ── Results ──────────────────────────────────────────────────
            yield Static("", id="tu_results")

        yield StatusBar(id="status_bar")
        yield Footer()

    # ------------------------------------------------------------------
    # Mount
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])
        self.query_one("#status_bar", StatusBar).set_text(
            "Enter paths to a game file and a Title Update, then press Check."
        )
        self.query_one("#game_path", Input).focus()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", StatusBar).set_text(msg)
        except Exception:
            pass

    def _render_info_block(self, label: str, info: StfsInfo) -> str:
        """Return a Rich markup string for one file's parsed STFS info."""
        if not info.ok:
            return f"[b]{label}[/b]\n  [red]{info.error}[/red]\n"
        return (
            f"[b]{label}[/b]\n"
            f"  Title Name : [cyan]{info.title_name}[/cyan]\n"
            f"  Title ID   : [yellow]{info.title_id}[/yellow]\n"
            f"  Media ID   : [yellow]{info.media_id}[/yellow]\n"
            f"  Magic      : [dim]{info.magic.strip()}[/dim]\n"
        )

    # ------------------------------------------------------------------
    # Button handler
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn_check":
            self.action_check()
        elif bid == "btn_reset":
            self._reset()
        elif bid in ("btn_back",):
            self.app.pop_screen()
        elif bid == "clear_game":
            self.query_one("#game_path", Input).value = ""
            self.query_one("#game_path", Input).focus()
        elif bid == "clear_tu":
            self.query_one("#tu_path", Input).value = ""
            self.query_one("#tu_path", Input).focus()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_check(self) -> None:
        game_path_str = self.query_one("#game_path", Input).value.strip().strip("'\"")
        tu_path_str = self.query_one("#tu_path", Input).value.strip().strip("'\"")

        results = self.query_one("#tu_results", Static)

        if not game_path_str and not tu_path_str:
            results.update("")
            self._set_status("Enter paths to both files first.")
            return

        if not game_path_str:
            self._set_status("Please enter the game file path.")
            return

        if not tu_path_str:
            self._set_status("Please enter the Title Update file path.")
            return

        self._set_status("Reading STFS headers…")

        game = read_stfs_info(game_path_str)
        tu = read_stfs_info(tu_path_str)

        output: list[str] = ["\n"]
        output.append(self._render_info_block("Game File", game))
        output.append("\n")
        output.append(self._render_info_block("Title Update", tu))
        output.append("\n")

        if not game.ok or not tu.ok:
            output.append("[red]Cannot determine compatibility — fix the errors above.[/red]")
            self._set_status("One or both files could not be read.")
        else:
            title_match = game.title_id == tu.title_id
            media_match = game.media_id == tu.media_id

            if title_match and media_match:
                output.append("[b green]✅  COMPATIBLE[/b green]")
                output.append(
                    "\n[dim]Title ID and Media ID both match — "
                    "this Title Update is compatible with the game.[/dim]"
                )
                self._set_status("Compatible ✅")
            else:
                output.append("[b red]❌  NOT COMPATIBLE[/b red]")
                if not title_match:
                    output.append(
                        f"\n  [red]✗ Title ID mismatch:[/red]  "
                        f"game [yellow]{game.title_id}[/yellow]  "
                        f"vs  TU [yellow]{tu.title_id}[/yellow]"
                    )
                if not media_match:
                    output.append(
                        f"\n  [red]✗ Media ID mismatch:[/red]  "
                        f"game [yellow]{game.media_id}[/yellow]  "
                        f"vs  TU [yellow]{tu.media_id}[/yellow]"
                    )
                self._set_status("Not compatible ❌")

        results.update("\n".join(output))

    def _reset(self) -> None:
        self.query_one("#game_path", Input).value = ""
        self.query_one("#tu_path", Input).value = ""
        self.query_one("#tu_results", Static).update("")
        self._set_status("Cleared.")
        self.query_one("#game_path", Input).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
