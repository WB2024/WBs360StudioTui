"""Torrent file selector — tree view with optional filter, submit to qBittorrent."""
from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Static, Tree
from textual.widgets.tree import TreeNode

from app.core.qbit_client import QbitAddError, QbitClient, QbitConfig, QbitConnectionError
from app.core.torrent_decoder import DecodedTorrent, TorrentTreeNode, format_size
from app.core.torrent_path import DownloadPathError, resolve_save_path
from app.core.torrent_selection import SelectionManager
from app.tui.widgets.status_bar import StatusBar


# ── Confirmation modal ──────────────────────────────────────────────────────

class ConfirmDownloadModal(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, count: int, size: int, save_path: str | None) -> None:
        super().__init__()
        self._count = count
        self._size = size
        self._save_path = save_path

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static("[b]Start Download?[/b]", id="confirm_title")
            yield Static(
                f"  Files selected:  {self._count}\n"
                f"  Total size:      {format_size(self._size)}\n"
                f"  Save path:       {self._save_path or '(qBittorrent default)'}",
                id="confirm_summary",
            )
            yield Static(
                "[yellow]This will add the torrent to qBittorrent and begin downloading "
                "the selected files.[/yellow]",
                id="confirm_warn",
            )
            with Horizontal(id="confirm_btns"):
                yield Button("Download", id="confirm_yes", variant="success")
                yield Button("Cancel", id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── Helper ──────────────────────────────────────────────────────────────────

def _node_matches_filter(node: TorrentTreeNode, query: str) -> bool:
    """True if *node* or any descendant's name contains *query* (case-insensitive)."""
    if query.lower() in node.name.lower():
        return True
    return any(_node_matches_filter(child, query) for child in node.children.values())


# ── Main selector screen ───────────────────────────────────────────────────

class TorrentSelectScreen(Screen):
    TITLE = "Select Files to Download"
    BINDINGS = [
        Binding("escape", "escape_action", "Back", show=True),
        Binding("space", "toggle", "Toggle", show=True),
        Binding("a", "select_all", "All", show=True),
        Binding("n", "select_none", "None", show=True),
        Binding("d", "download", "Download", show=True),
        Binding("s", "focus_search", "Search", show=True, priority=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, torrent: DecodedTorrent, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._torrent = torrent
        self._selection = SelectionManager.from_tree(torrent.tree, default_select_all=True)
        self._node_map: dict[int, TorrentTreeNode] = {}
        self._search_query: str = ""

    # ── layout ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="tsel_summary_row"):
            yield Static(
                f"[b]{self._torrent.name}[/b]   "
                f"({self._torrent.file_count} files, "
                f"{format_size(self._torrent.total_size)})",
                id="tsel_summary",
            )
        with Horizontal(id="tsel_actions"):
            yield Button("Select All [A]", id="tsel_all")
            yield Button("Select None [N]", id="tsel_none")
            yield Button("Download [D]", id="tsel_download", variant="success")
            yield Button("Back [Esc]", id="tsel_back")
        yield Input(placeholder="Filter files/folders… (Esc to clear)", id="tsel_search")
        yield Tree("(loading)", id="tsel_tree")
        yield StatusBar(id="status_bar")
        yield Footer()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one("#tsel_search", Input).display = False
        self._rebuild_tree()
        self._update_status()
        self.query_one("#tsel_tree", Tree).focus()

    # ── tree ──────────────────────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#tsel_tree", Tree)
        self._node_map.clear()
        tree.clear()
        root = tree.root
        root.data = self._torrent.tree
        self._node_map[id(root)] = self._torrent.tree
        root.set_label(f"[b]{self._torrent.name}[/b]")
        self._build_subtree(root, self._torrent.tree)
        root.expand()

    def _build_subtree(self, parent_tnode: TreeNode, t_node: TorrentTreeNode) -> None:
        query = self._search_query
        children = sorted(
            t_node.children.values(),
            key=lambda c: (not c.is_dir, c.name.lower()),
        )
        for child in children:
            if query and not _node_matches_filter(child, query):
                continue
            sub = parent_tnode.add(
                self._label_for(child),
                data=child,
                expand=bool(query),
            )
            self._node_map[id(sub)] = child
            if child.is_dir:
                self._build_subtree(sub, child)
            else:
                sub.allow_expand = False

    def _label_for(self, node: TorrentTreeNode) -> str:
        state = self._selection.node_state(node)
        if state in ("all", "on"):
            box = "[green][x][/green]"
        elif state == "partial":
            box = "[yellow][~][/yellow]"
        else:
            box = "[ ]"
        size_str = format_size(node.size)
        if node.is_dir:
            return f"{box} {node.name}/  [dim]({size_str})[/dim]"
        return f"{box} {node.name}  [dim]({size_str})[/dim]"

    def _refresh_labels(self, tnode: TreeNode) -> None:
        data = self._node_map.get(id(tnode))
        if data is not None:
            tnode.set_label(self._label_for(data))
        for child in tnode.children:
            self._refresh_labels(child)

    # ── status ────────────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        count = self._selection.selected_count()
        size = self._selection.selected_size(self._torrent.files)
        if self._search_query:
            hint = f"  |  Filter: '{self._search_query}'  (Esc to clear)"
        else:
            hint = "  |  S=search  Space=toggle  A=all  N=none  D=download"
        self.query_one("#status_bar", StatusBar).set_text(
            f"Selected: {count}/{self._torrent.file_count} files  |  {format_size(size)}{hint}"
        )

    # ── events ────────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "tsel_search":
            self._search_query = event.value.strip()
            self._rebuild_tree()
            self._update_status()

    def on_key(self, event: Any) -> None:
        """Esc inside the search input clears it and hides the bar."""
        if event.key == "escape":
            inp = self.query_one("#tsel_search", Input)
            if inp.has_focus or self._search_query:
                inp.value = ""
                self._search_query = ""
                inp.display = False
                self._rebuild_tree()
                self._update_status()
                self.query_one("#tsel_tree", Tree).focus()
                event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "tsel_all":
            self.action_select_all()
        elif bid == "tsel_none":
            self.action_select_none()
        elif bid == "tsel_download":
            asyncio.ensure_future(self._do_download())
        elif bid == "tsel_back":
            self.app.pop_screen()

    # ── actions ───────────────────────────────────────────────────────────────

    def action_escape_action(self) -> None:
        inp = self.query_one("#tsel_search", Input)
        if self._search_query or inp.has_focus:
            inp.value = ""
            self._search_query = ""
            inp.display = False
            self._rebuild_tree()
            self._update_status()
            self.query_one("#tsel_tree", Tree).focus()
        else:
            self.app.pop_screen()

    def action_focus_search(self) -> None:
        inp = self.query_one("#tsel_search", Input)
        inp.display = True
        inp.focus()

    def action_toggle(self) -> None:
        tree = self.query_one("#tsel_tree", Tree)
        cursor = tree.cursor_node
        if cursor is None:
            return
        data = self._node_map.get(id(cursor))
        if data is None:
            return
        self._selection.toggle_node(data)
        self._refresh_labels(tree.root)
        self._update_status()

    def action_select_all(self) -> None:
        self._selection.select_all()
        self._refresh_labels(self.query_one("#tsel_tree", Tree).root)
        self._update_status()

    def action_select_none(self) -> None:
        self._selection.deselect_all()
        self._refresh_labels(self.query_one("#tsel_tree", Tree).root)
        self._update_status()

    def action_download(self) -> None:
        asyncio.ensure_future(self._do_download())

    def action_quit(self) -> None:
        self.app.exit()

    # ── download ──────────────────────────────────────────────────────────────

    async def _do_download(self) -> None:
        if self._selection.selected_count() == 0:
            self.query_one("#status_bar", StatusBar).set_text(
                "No files selected — pick at least one file with Space."
            )
            return

        settings = self.app.settings  # type: ignore[attr-defined]

        try:
            save_path = resolve_save_path(
                runtime=None,
                config=settings.torrent_download_folder,
                create=True,
            )
        except DownloadPathError as exc:
            self.query_one("#status_bar", StatusBar).set_text(f"Save path error: {exc}")
            return

        size = self._selection.selected_size(self._torrent.files)
        count = self._selection.selected_count()

        confirmed = await self.app.push_screen_wait(
            ConfirmDownloadModal(count=count, size=size, save_path=save_path)
        )
        if not confirmed:
            return

        for bid in ("tsel_all", "tsel_none", "tsel_download"):
            try:
                self.query_one(f"#{bid}", Button).disabled = True
            except Exception:
                pass

        self.query_one("#status_bar", StatusBar).set_text("Connecting to qBittorrent…")

        cfg = QbitConfig(
            host=settings.qbit_host,
            port=settings.qbit_port,
            username=settings.qbit_username,
            password=settings.qbit_password,
        )
        client = QbitClient(cfg)
        try:
            await client.connect()
        except QbitConnectionError as exc:
            self.query_one("#status_bar", StatusBar).set_text(
                f"qBittorrent connection failed: {exc}"
            )
            self._reenable_buttons()
            return

        self.query_one("#status_bar", StatusBar).set_text(
            "Adding torrent (paused) and applying file priorities…"
        )

        try:
            info_hash = await client.add_torrent_selective(
                torrent_path=str(self._torrent.source_path),
                info_hash=self._torrent.info_hash,
                all_indices=sorted(self._selection.all_indices),
                selected_indices=self._selection.selected_indices(),
                save_path=save_path,
            )
        except QbitAddError as exc:
            self.query_one("#status_bar", StatusBar).set_text(f"Failed: {exc}")
            self._reenable_buttons()
            return

        self.query_one("#status_bar", StatusBar).set_text(
            f"Download started — {count} file(s), {format_size(size)} → "
            f"{save_path or 'qBittorrent default'}  |  hash: {info_hash[:12]}…"
        )
        self._reenable_buttons()

    def _reenable_buttons(self) -> None:
        for bid in ("tsel_all", "tsel_none", "tsel_download"):
            try:
                self.query_one(f"#{bid}", Button).disabled = False
            except Exception:
                pass
