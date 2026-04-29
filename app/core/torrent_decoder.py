"""Decode .torrent files and build a navigable file tree.

Wraps torf for decoding. Builds a hierarchical tree where every file leaf
carries its index (matching the order in info.files), required by the
qBittorrent API to set per-file priorities.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from torf import Torrent


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TorrentFileEntry:
    """One file inside a torrent."""
    index: int
    path: tuple[str, ...]      # forward-slash-joined components
    size: int                  # bytes

    @property
    def name(self) -> str:
        return self.path[-1] if self.path else ""

    @property
    def display_path(self) -> str:
        return "/".join(self.path)


@dataclass
class TorrentTreeNode:
    """A folder or file node in the torrent tree."""
    name: str
    is_dir: bool
    size: int = 0
    file_index: int | None = None          # set on leaves only
    children: dict[str, "TorrentTreeNode"] = field(default_factory=dict)

    def add_file(self, path: tuple[str, ...], size: int, index: int) -> None:
        if not path:
            return
        head, rest = path[0], path[1:]
        if not rest:
            # leaf
            self.children[head] = TorrentTreeNode(
                name=head, is_dir=False, size=size, file_index=index
            )
        else:
            child = self.children.get(head)
            if child is None:
                child = TorrentTreeNode(name=head, is_dir=True)
                self.children[head] = child
            child.add_file(rest, size, index)

    def compute_sizes(self) -> int:
        if not self.is_dir:
            return self.size
        total = 0
        for c in self.children.values():
            total += c.compute_sizes()
        self.size = total
        return total

    def iter_files(self) -> Iterable["TorrentTreeNode"]:
        if not self.is_dir:
            yield self
            return
        for c in self.children.values():
            yield from c.iter_files()

    def all_file_indices(self) -> list[int]:
        return [
            f.file_index for f in self.iter_files() if f.file_index is not None
        ]


@dataclass
class DecodedTorrent:
    name: str
    info_hash: str
    total_size: int
    file_count: int
    trackers: list[str]
    comment: str | None
    created_by: str | None
    files: list[TorrentFileEntry]
    tree: TorrentTreeNode
    source_path: Path


# ── Decoder ──────────────────────────────────────────────────────────────────

def decode_torrent(torrent_path: str | os.PathLike) -> DecodedTorrent:
    """Read and decode a .torrent file from disk.

    Raises:
        FileNotFoundError: file missing
        ValueError: file unreadable or not a valid torrent
    """
    path = Path(torrent_path)
    if not path.exists():
        raise FileNotFoundError(f"Torrent file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    try:
        t = Torrent.read(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to decode torrent: {exc}") from exc

    files: list[TorrentFileEntry] = []
    if t.files:
        # torf normalises to TorrentFile objects (path-like) — iterate in order.
        for index, f in enumerate(t.files):
            # f is a path-like; split into components, dropping the torrent name
            # because torf prepends it. We want paths *relative* to the torrent
            # root for tree-building.
            parts = tuple(str(p) for p in f.parts)
            if parts and parts[0] == t.name:
                parts = parts[1:]
            if not parts:
                parts = (t.name,)
            files.append(
                TorrentFileEntry(index=index, path=parts, size=int(f.size))
            )
    else:
        # Single-file fallback (shouldn't normally happen with torf, but guard).
        files.append(
            TorrentFileEntry(index=0, path=(t.name,), size=int(t.size or 0))
        )

    tree = TorrentTreeNode(name=t.name, is_dir=True)
    for f in files:
        tree.add_file(f.path, f.size, f.index)
    tree.compute_sizes()

    trackers: list[str] = []
    try:
        for tier in (t.trackers or []):
            for url in tier:
                trackers.append(str(url))
    except Exception:
        pass

    return DecodedTorrent(
        name=str(t.name),
        info_hash=str(t.infohash),
        total_size=int(t.size or sum(f.size for f in files)),
        file_count=len(files),
        trackers=trackers,
        comment=str(t.comment) if t.comment else None,
        created_by=str(t.created_by) if t.created_by else None,
        files=files,
        tree=tree,
        source_path=path,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def list_torrent_files(folder: str | os.PathLike) -> list[Path]:
    """Return all .torrent files in a folder (non-recursive), sorted by name."""
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return []
    return sorted(
        [f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".torrent"],
        key=lambda x: x.name.lower(),
    )


def format_size(num: int) -> str:
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} PB"
