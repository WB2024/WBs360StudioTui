"""XboxUnity.net Title Update search and install screen."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from app.core import xboxunity
from app.core.tu_scanner import scan_local_title_updates
from app.models.title_update import TitleUpdateItem
from app.tui.widgets.connection_bar import ConnectionBar
from app.tui.widgets.mod_detail import ModDetail
from app.tui.widgets.mod_table import ModTable
from app.tui.widgets.progress_modal import ProgressModal
from app.tui.widgets.status_bar import StatusBar


# ---------------------------------------------------------------------------
# Helpers (re-exported from title_updates to avoid duplication)
# ---------------------------------------------------------------------------

def _fmt_bytes(b: int) -> str:
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"


def _drive_from_path(xbox_path: str) -> str:
    if not xbox_path:
        return "Usb1"
    part = xbox_path.replace("\\", "/").split("/")[0].rstrip(":")
    return part if part else "Usb1"


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class XboxUnityTuScreen(Screen):
    """Search XboxUnity.net for Title Updates and install them to the console."""

    TITLE = "XboxUnity — Title Updates Online"

    BINDINGS = [
        Binding("escape", "back_or_search", "Back", show=True),
        Binding("i", "install", "Install", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    # ── Init ─────────────────────────────────────────────────────────────

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._results: list[xboxunity.XboxUnityTitle] = []
        self._tu_entries: list[xboxunity.XboxUnityTuEntry] = []
        self._selected_title: xboxunity.XboxUnityTitle | None = None
        self._stage = "search"  # "search" | "tu_list"

    # ── Compose ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ConnectionBar(id="conn_bar")

        # ── Stage 1: Search ──────────────────────────────────────────────
        with Horizontal(id="xu_search_stage"):
            with Vertical(id="xu_search_left"):
                with Horizontal(id="xu_search_bar"):
                    yield Input(
                        placeholder="Search by name or Title ID…",
                        id="xu_search_input",
                    )
                    yield Button("Search", id="xu_search_btn", variant="primary")
                yield Static(
                    "[dim]── From Library ──[/dim]",
                    id="xu_lib_label",
                )
                yield ListView(id="xu_library_list")

            with Vertical(id="xu_search_right"):
                yield Static(
                    "[dim]Enter a game name or Title ID above, "
                    "or select from your library to browse its online Title Updates.[/dim]",
                    id="xu_search_hint",
                )
                yield ModTable(id="xu_result_table")

        # ── Stage 2: TU list (hidden until a game is chosen) ─────────────
        with Horizontal(id="xu_tu_stage"):
            with Vertical(id="xu_tu_left"):
                with Horizontal(id="xu_tu_toolbar"):
                    yield Button("← Back", id="xu_back_btn")
                    yield Button("Install [I]", id="xu_install_btn", variant="primary")
                yield Static("", id="xu_game_title")
                yield ModTable(id="xu_tu_table")

            with Vertical(id="xu_tu_right"):
                yield ModDetail(id="xu_tu_detail")

        yield StatusBar(id="status_bar")
        yield Footer()

    # ── Mount ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_conn_bar()
        self._populate_library()
        self._set_stage("search")

    def _refresh_conn_bar(self) -> None:
        bar = self.query_one("#conn_bar", ConnectionBar)
        s = self.app.connection_status  # type: ignore[attr-defined]
        bar.set_status(connected=s["connected"], text=s["text"])

    def _populate_library(self) -> None:
        library: dict[str, str] = self.app.library  # type: ignore[attr-defined]
        db = self.app.db  # type: ignore[attr-defined]
        lv = self.query_one("#xu_library_list", ListView)
        lv.clear()
        for tid in sorted(library.keys()):
            name = db.resolve_game_title(tid)
            item = ListItem(Label(f"[b]{name}[/b]  [dim]{tid}[/dim]"))
            item._tid = tid  # type: ignore[attr-defined]
            lv.append(item)
        if not library:
            self.query_one("#xu_lib_label", Static).update(
                "[dim]── From Library (empty — run a Library Scan first) ──[/dim]"
            )

    # ── Stage switching ──────────────────────────────────────────────────

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        search = self.query_one("#xu_search_stage", Horizontal)
        tu = self.query_one("#xu_tu_stage", Horizontal)
        search.display = (stage == "search")
        tu.display = (stage == "tu_list")

    # ── Search stage events ──────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "xu_search_input":
            self._do_search(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "xu_search_btn":
            self._do_search(self.query_one("#xu_search_input", Input).value.strip())
        elif bid == "xu_back_btn":
            self._set_stage("search")
        elif bid == "xu_install_btn":
            self.action_install()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Library game clicked → jump straight to TU list for that Title ID."""
        if event.list_view.id != "xu_library_list":
            return
        item = event.item
        if item is not None and hasattr(item, "_tid"):
            tid: str = item._tid  # type: ignore[attr-defined]
            db = self.app.db  # type: ignore[attr-defined]
            name = db.resolve_game_title(tid)
            self.query_one("#xu_search_input", Input).value = tid
            self._fetch_tu_list_for(
                xboxunity.XboxUnityTitle(
                    title_id=tid,
                    name=name,
                    title_type="360",
                    update_count=0,
                )
            )

    def _do_search(self, query: str) -> None:
        if not query:
            return
        self.query_one("#status_bar", StatusBar).set_text(
            f"Searching XboxUnity for '{query}'…"
        )
        self.query_one("#xu_search_btn", Button).disabled = True
        self.run_worker(self._search_worker(query), exclusive=True, exit_on_error=False)

    async def _search_worker(self, query: str) -> None:
        try:
            results = await xboxunity.search_titles(query)
        except Exception as e:
            self._search_done([], f"Search failed: {e}")
            return
        self._search_done(results, "")

    def _search_done(
        self, results: list[xboxunity.XboxUnityTitle], error: str
    ) -> None:
        self.query_one("#xu_search_btn", Button).disabled = False
        if error:
            self.query_one("#status_bar", StatusBar).set_text(error)
            return

        self._results = results
        table = self.query_one("#xu_result_table", ModTable)

        if not results:
            table.populate(["Name", "Title ID", "Type", "Updates"], [])
            self.query_one("#status_bar", StatusBar).set_text("No results found.")
            return

        rows: list[tuple[Any, list[str]]] = [
            (r, [r.name, r.title_id, r.title_type, str(r.update_count)])
            for r in results
        ]
        table.populate(["Name", "Title ID", "Type", "Updates"], rows)
        self.query_one("#xu_search_hint", Static).update(
            "[dim]Select a game and press Enter to view its available Title Updates.[/dim]"
        )
        self.query_one("#status_bar", StatusBar).set_text(
            f"{len(results)} game(s) found. Press Enter to view Title Updates."
        )

    # ── Result table selection ────────────────────────────────────────────

    def on_data_table_row_selected(self, event: Any) -> None:
        ctrl_id = getattr(event.control, "id", None)
        if ctrl_id == "xu_result_table":
            table = self.query_one("#xu_result_table", ModTable)
            item = table.get_item(event.row_key.value if event.row_key else None)
            if isinstance(item, xboxunity.XboxUnityTitle):
                self._fetch_tu_list_for(item)

    # ── TU table highlight → detail pane ─────────────────────────────────

    def on_data_table_row_highlighted(self, event: Any) -> None:
        ctrl_id = getattr(event.control, "id", None)
        if ctrl_id == "xu_tu_table":
            table = self.query_one("#xu_tu_table", ModTable)
            entry = table.get_item(event.row_key.value if event.row_key else None)
            if isinstance(entry, xboxunity.XboxUnityTuEntry):
                self._show_tu_detail(entry)

    # ── TU list stage logic ───────────────────────────────────────────────

    def _fetch_tu_list_for(self, title: xboxunity.XboxUnityTitle) -> None:
        self._selected_title = title
        self.query_one("#status_bar", StatusBar).set_text(
            f"Fetching Title Updates for {title.name}…"
        )
        self.run_worker(
            self._tu_list_worker(title.title_id),
            exclusive=True,
            exit_on_error=False,
        )

    async def _tu_list_worker(self, title_id: str) -> None:
        try:
            entries = await xboxunity.get_title_updates(title_id)
        except Exception as e:
            self._tu_list_done([], f"Failed to fetch Title Updates: {e}")
            return
        self._tu_list_done(entries, "")

    def _tu_list_done(
        self, entries: list[xboxunity.XboxUnityTuEntry], error: str
    ) -> None:
        self._tu_entries = entries
        title = self._selected_title

        if error:
            self.query_one("#status_bar", StatusBar).set_text(error)
            return

        self._set_stage("tu_list")
        game_label = (
            f"[b cyan]{title.name}[/b cyan]  [dim]{title.title_id}[/dim]"
            if title else ""
        )
        self.query_one("#xu_game_title", Static).update(game_label)

        if not entries:
            self.query_one("#xu_tu_table", ModTable).populate(
                ["Version", "Media ID", "Size", "Date"], []
            )
            self.query_one("#status_bar", StatusBar).set_text(
                "No Title Updates found for this game on XboxUnity."
            )
            return

        rows: list[tuple[Any, list[str]]] = [
            (e, [e.version_str, e.media_id, e.size_str, e.upload_date])
            for e in entries
        ]
        self.query_one("#xu_tu_table", ModTable).populate(
            ["Version", "Media ID", "Size", "Date"], rows
        )
        self.query_one("#status_bar", StatusBar).set_text(
            f"{len(entries)} Title Update(s) — select one and press [I] to download & install."
        )

    def _show_tu_detail(self, entry: xboxunity.XboxUnityTuEntry) -> None:
        title = self._selected_title
        tid = title.title_id if title else "?"
        game_drive = _drive_from_path(
            self.app.settings.game_install_path or "Usb1"  # type: ignore[attr-defined]
        )
        fields: list[tuple[str, Any]] = [
            ("Game", entry.name),
            ("Title ID", tid),
            ("Version", entry.version_str),
            ("Media ID", entry.media_id),
            ("Size", entry.size_str),
            ("Upload Date", entry.upload_date),
            ("Base Version", entry.base_version),
            ("SHA1", entry.sha1_hash),
            ("Install Destination",
             f"{game_drive}:\\Content\\0000000000000000\\{tid}\\000B0000\\"),
        ]
        self.query_one("#xu_tu_detail", ModDetail).show_item(fields)

    # ── Actions ──────────────────────────────────────────────────────────

    def action_back_or_search(self) -> None:
        if self._stage == "tu_list":
            self._set_stage("search")
        else:
            self.app.pop_screen()

    def action_install(self) -> None:
        if self._stage != "tu_list":
            return
        table = self.query_one("#xu_tu_table", ModTable)
        entry = table.selected_item()
        if not isinstance(entry, xboxunity.XboxUnityTuEntry):
            self.query_one("#status_bar", StatusBar).set_text(
                "Select a Title Update to install."
            )
            return
        self.app.run_worker(
            self._download_and_install(entry, self._selected_title),
            exclusive=False,
            exit_on_error=False,
        )

    def action_quit(self) -> None:
        self.app.exit()

    # ── Download + install flow ───────────────────────────────────────────

    async def _download_and_install(
        self,
        entry: xboxunity.XboxUnityTuEntry,
        title: xboxunity.XboxUnityTitle | None,
    ) -> None:
        """Download the TU from XboxUnity, then run the existing install flow."""
        # Lazy import to avoid circular dependency
        from app.tui.screens.title_updates import run_tu_install_flow

        tid = title.title_id if title else (entry.name or "UnknownTitle")
        settings = self.app.settings  # type: ignore[attr-defined]

        # Resolve where to save the downloaded TU
        tu_base = getattr(settings, "local_title_updates_path", "") or ""
        if tu_base:
            dest_dir = Path(tu_base) / tid
        else:
            from app.core.tu_scanner import _LOCAL_TU_DIR
            dest_dir = _LOCAL_TU_DIR / tid

        # --- Download phase ---
        modal = ProgressModal(
            f"Downloading: {entry.name}  {entry.version_str}"
        )
        await self.app.push_screen(modal)

        _start = time.monotonic()

        def dl_progress(done: int, total: int) -> None:
            elapsed = time.monotonic() - _start
            pct = (done / total * 100) if total > 0 else 0
            speed = done / elapsed if elapsed > 1 else 0
            speed_str = _fmt_bytes(int(speed)) + "/s" if elapsed > 1 else "…"
            modal.set_stage(f"Downloading… {pct:.1f}%", done, total or done)
            modal.set_detail(
                _fmt_bytes(done)
                + (f" of {_fmt_bytes(total)}" if total else "")
                + f"  •  {speed_str}"
            )

        try:
            local_path = await xboxunity.download_title_update(
                entry, dest_dir, tid, progress_callback=dl_progress
            )
        except Exception as e:
            modal.set_done(f"Download failed: {e}", success=False)
            return  # User closes modal; install aborted

        # Auto-dismiss the download modal and proceed
        self.app.pop_screen()

        # --- Build TitleUpdateItem ---
        # Try to find the downloaded file via the STFS scanner (most reliable)
        tu_item: TitleUpdateItem | None = None
        try:
            scanned = scan_local_title_updates(dest_dir)
            for candidate in scanned:
                if candidate.local_path == local_path:
                    tu_item = candidate
                    break
        except Exception:
            pass

        if tu_item is None:
            # Fallback: construct from known info; compat modal will read STFS header itself
            try:
                v = int(entry.version)
            except (ValueError, TypeError):
                v = 0
            tu_item = TitleUpdateItem(
                title_id=tid,
                display_name=entry.name or tid,
                version=v,
                local_path=local_path,
            )

        # --- Run existing compat check + install flow ---
        await run_tu_install_flow(self.app, tu_item)
