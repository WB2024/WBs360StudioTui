"""Selection manager for torrent files.

Tracks which file indices the user has selected. Default = all selected
(mirrors GUI client behaviour). Folder-level select/deselect cascades to
children. Output is a (selected, skipped) tuple of index lists ready for
the qBittorrent priority API.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.torrent_decoder import TorrentTreeNode


@dataclass
class SelectionManager:
    all_indices: set[int]
    selected: set[int] = field(default_factory=set)

    @classmethod
    def from_tree(cls, tree: TorrentTreeNode, default_select_all: bool = True) -> "SelectionManager":
        idx = set(tree.all_file_indices())
        sm = cls(all_indices=idx)
        if default_select_all:
            sm.selected = set(idx)
        return sm

    # ── single-file ops ──
    def is_selected(self, index: int) -> bool:
        return index in self.selected

    def select(self, index: int) -> None:
        if index in self.all_indices:
            self.selected.add(index)

    def deselect(self, index: int) -> None:
        self.selected.discard(index)

    def toggle(self, index: int) -> None:
        if index in self.selected:
            self.selected.discard(index)
        elif index in self.all_indices:
            self.selected.add(index)

    # ── folder ops ──
    def select_node(self, node: TorrentTreeNode) -> None:
        for i in node.all_file_indices():
            self.selected.add(i)

    def deselect_node(self, node: TorrentTreeNode) -> None:
        for i in node.all_file_indices():
            self.selected.discard(i)

    def toggle_node(self, node: TorrentTreeNode) -> None:
        idxs = node.all_file_indices()
        if not idxs:
            return
        # If every child is selected → deselect all; else select all.
        if all(i in self.selected for i in idxs):
            for i in idxs:
                self.selected.discard(i)
        else:
            for i in idxs:
                self.selected.add(i)

    def node_state(self, node: TorrentTreeNode) -> str:
        """Return 'all' / 'none' / 'partial' for a folder, or 'on'/'off' for a file."""
        if not node.is_dir:
            return "on" if node.file_index in self.selected else "off"
        idxs = node.all_file_indices()
        if not idxs:
            return "none"
        sel = sum(1 for i in idxs if i in self.selected)
        if sel == 0:
            return "none"
        if sel == len(idxs):
            return "all"
        return "partial"

    # ── bulk ops ──
    def select_all(self) -> None:
        self.selected = set(self.all_indices)

    def deselect_all(self) -> None:
        self.selected.clear()

    # ── output ──
    def selected_indices(self) -> list[int]:
        return sorted(self.selected)

    def skipped_indices(self) -> list[int]:
        return sorted(self.all_indices - self.selected)

    def selected_size(self, files: list) -> int:
        """Sum size in bytes of selected entries (files: list[TorrentFileEntry])."""
        return sum(f.size for f in files if f.index in self.selected)

    def selected_count(self) -> int:
        return len(self.selected)
