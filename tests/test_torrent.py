"""Tests for the torrent file selector feature."""
from __future__ import annotations

from pathlib import Path

import pytest
import torf

from app.core.torrent_decoder import (
    DecodedTorrent,
    decode_torrent,
    format_size,
    list_torrent_files,
)
from app.core.torrent_path import DownloadPathError, resolve_save_path
from app.core.torrent_selection import SelectionManager


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def multi_file_torrent(tmp_path: Path) -> Path:
    """Create a multi-file .torrent for testing.

    Returns the path to the .torrent file.
    """
    src = tmp_path / "MyGame"
    (src / "disc1").mkdir(parents=True)
    (src / "disc2").mkdir(parents=True)
    (src / "extras").mkdir(parents=True)

    (src / "readme.txt").write_bytes(b"hello world\n")
    (src / "disc1" / "iso.bin").write_bytes(b"x" * 4096)
    (src / "disc1" / "iso.cue").write_bytes(b"cue\n")
    (src / "disc2" / "iso.bin").write_bytes(b"y" * 8192)
    (src / "extras" / "manual.pdf").write_bytes(b"z" * 2048)

    t = torf.Torrent(
        path=str(src),
        trackers=["http://example.com/announce"],
        comment="test torrent",
        created_by="pytest",
        private=False,
    )
    t.generate()
    out = tmp_path / "MyGame.torrent"
    t.write(str(out))
    return out


@pytest.fixture
def decoded(multi_file_torrent: Path) -> DecodedTorrent:
    return decode_torrent(multi_file_torrent)


# ── Decoder ─────────────────────────────────────────────────────────────────

class TestDecoder:
    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            decode_torrent(tmp_path / "nope.torrent")

    def test_invalid_file(self, tmp_path: Path):
        bad = tmp_path / "bad.torrent"
        bad.write_bytes(b"not a torrent at all")
        with pytest.raises(ValueError):
            decode_torrent(bad)

    def test_basic_fields(self, decoded: DecodedTorrent):
        assert decoded.name == "MyGame"
        assert decoded.file_count == 5
        assert decoded.total_size > 0
        assert len(decoded.info_hash) == 40  # SHA1 hex
        assert "http://example.com/announce" in decoded.trackers
        assert decoded.comment == "test torrent"

    def test_files_have_unique_indices(self, decoded: DecodedTorrent):
        idx = [f.index for f in decoded.files]
        assert sorted(idx) == list(range(len(idx)))

    def test_paths_relative_to_torrent_root(self, decoded: DecodedTorrent):
        # Paths should NOT include the torrent name.
        for f in decoded.files:
            assert f.path[0] != "MyGame", f.path

    def test_tree_built(self, decoded: DecodedTorrent):
        tree = decoded.tree
        assert tree.is_dir
        assert tree.name == "MyGame"
        names = set(tree.children.keys())
        assert names == {"disc1", "disc2", "extras", "readme.txt"}

    def test_tree_size_aggregates(self, decoded: DecodedTorrent):
        # Root size equals total
        assert decoded.tree.size == decoded.total_size
        # disc1 size = 4096 + 4 = 4100
        d1 = decoded.tree.children["disc1"]
        assert d1.size == 4096 + 4

    def test_all_file_indices_collects_leaves(self, decoded: DecodedTorrent):
        idx = sorted(decoded.tree.all_file_indices())
        assert idx == sorted(f.index for f in decoded.files)


# ── list_torrent_files ─────────────────────────────────────────────────────

class TestListTorrentFiles:
    def test_empty_dir(self, tmp_path: Path):
        assert list_torrent_files(tmp_path) == []

    def test_missing_dir(self, tmp_path: Path):
        assert list_torrent_files(tmp_path / "nope") == []

    def test_lists_only_torrent_files(self, tmp_path: Path):
        (tmp_path / "a.torrent").write_bytes(b"")
        (tmp_path / "b.txt").write_bytes(b"")
        (tmp_path / "C.TORRENT").write_bytes(b"")
        names = [f.name for f in list_torrent_files(tmp_path)]
        # case-insensitive extension match, sorted by lowercase name
        assert names == ["a.torrent", "C.TORRENT"]


# ── format_size ────────────────────────────────────────────────────────────

class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"

    def test_kb(self):
        assert format_size(2048).endswith("KB")

    def test_gb(self):
        assert "GB" in format_size(5 * 1024**3)


# ── SelectionManager ───────────────────────────────────────────────────────

class TestSelection:
    def test_default_select_all(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=True)
        assert sm.selected_count() == decoded.file_count
        assert sm.skipped_indices() == []

    def test_default_select_none(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=False)
        assert sm.selected_count() == 0
        assert sm.selected_indices() == []

    def test_toggle_single(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree)
        idx = decoded.files[0].index
        sm.toggle(idx)
        assert not sm.is_selected(idx)
        sm.toggle(idx)
        assert sm.is_selected(idx)

    def test_toggle_node_folder_cascades(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree)
        disc1 = decoded.tree.children["disc1"]
        # Currently all selected. Toggle disc1 → should deselect both disc1 children.
        sm.toggle_node(disc1)
        for i in disc1.all_file_indices():
            assert not sm.is_selected(i)
        # Toggle again → reselect.
        sm.toggle_node(disc1)
        for i in disc1.all_file_indices():
            assert sm.is_selected(i)

    def test_node_state(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=True)
        disc1 = decoded.tree.children["disc1"]
        assert sm.node_state(disc1) == "all"
        sm.deselect_node(disc1)
        assert sm.node_state(disc1) == "none"
        # Partially select
        sm.select(disc1.all_file_indices()[0])
        assert sm.node_state(disc1) == "partial"

    def test_node_state_for_file(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree)
        readme = decoded.tree.children["readme.txt"]
        assert sm.node_state(readme) == "on"
        sm.deselect(readme.file_index)
        assert sm.node_state(readme) == "off"

    def test_select_all_deselect_all(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=False)
        sm.select_all()
        assert sm.selected_count() == decoded.file_count
        sm.deselect_all()
        assert sm.selected_count() == 0

    def test_selected_size(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=True)
        assert sm.selected_size(decoded.files) == decoded.total_size
        sm.deselect_all()
        assert sm.selected_size(decoded.files) == 0

    def test_indices_are_sorted_lists(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree)
        sel = sm.selected_indices()
        assert sel == sorted(sel)

    def test_invalid_index_ignored(self, decoded: DecodedTorrent):
        sm = SelectionManager.from_tree(decoded.tree, default_select_all=False)
        sm.select(9999)  # not a real index
        assert 9999 not in sm.selected
        assert sm.selected_count() == 0


# ── Path resolver ──────────────────────────────────────────────────────────

class TestPath:
    def test_no_paths_returns_none(self):
        assert resolve_save_path(None, None) is None
        assert resolve_save_path("", "") is None

    def test_runtime_overrides_config(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        result = resolve_save_path(str(a), str(b))
        assert result == str(a.resolve())

    def test_falls_back_to_config(self, tmp_path: Path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        result = resolve_save_path(None, str(cfg))
        assert result == str(cfg.resolve())

    def test_creates_missing_dir(self, tmp_path: Path):
        target = tmp_path / "new_dir" / "nested"
        result = resolve_save_path(str(target), None, create=True)
        assert result is not None
        assert Path(result).exists()
        assert Path(result).is_dir()

    def test_no_create_raises(self, tmp_path: Path):
        target = tmp_path / "nope"
        with pytest.raises(DownloadPathError):
            resolve_save_path(str(target), None, create=False)

    def test_file_path_rejected(self, tmp_path: Path):
        f = tmp_path / "afile.txt"
        f.write_bytes(b"x")
        with pytest.raises(DownloadPathError):
            resolve_save_path(str(f), None, create=False)


# ── qBittorrent flow (logic-only, no real network) ──────────────────────────

class _FakeQbitClient:
    """Recording stub mimicking qbittorrentapi.Client surface used by QbitClient."""

    def __init__(self, *a, **kw):
        self.calls: list = []
        self.added: list = []
        self._registered: bool = False

        # nested namespaces accessed by QbitClient
        class _App:
            version = "fake-v5.0.0"
        self.app = _App()

    def auth_log_in(self): pass

    def torrents_add(self, **kwargs):
        self.added.append(kwargs)
        self._registered = True
        return "Ok."

    def torrents_info(self, torrent_hashes=None):
        if not self._registered:
            return []
        class _Info:
            name = "fake"
            state = "pausedDL"
            progress = 0.0
            dlspeed = 0
            downloaded = 0
            size = 100
            hash = torrent_hashes
        return [_Info()]

    def torrents_file_priority(self, torrent_hash, file_ids, priority):
        self.calls.append(("priority", torrent_hash, sorted(file_ids), priority))

    def torrents_resume(self, torrent_hashes):
        self.calls.append(("resume", torrent_hashes))


@pytest.mark.asyncio
async def test_qbit_add_flow(monkeypatch, tmp_path):
    """The add flow must: add paused → set skips → set selected → resume, in order."""
    from app.core import qbit_client as qmod

    fake = _FakeQbitClient()
    monkeypatch.setattr(qmod.qbittorrentapi, "Client", lambda *a, **kw: fake)

    cfg = qmod.QbitConfig(host="x", port=1, username="u", password="p")
    client = qmod.QbitClient(cfg)
    await client.connect()

    fake_torrent = tmp_path / "x.torrent"
    fake_torrent.write_bytes(b"")  # path just needs to exist for the kwarg

    info_hash = "a" * 40
    await client.add_torrent_selective(
        torrent_path=str(fake_torrent),
        info_hash=info_hash,
        all_indices=[0, 1, 2, 3],
        selected_indices=[1, 3],
        save_path="/tmp/out",
        wait_seconds=2.0,
        poll_interval=0.05,
    )

    # Must have added paused with save_path
    assert fake.added[0]["is_paused"] is True
    assert fake.added[0]["save_path"] == "/tmp/out"
    assert fake.added[0]["torrent_files"] == str(fake_torrent)

    # Order: priority(skip=0) → priority(sel=1) → resume
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["priority", "priority", "resume"]

    # Skip call: indices [0, 2] at priority 0
    assert fake.calls[0] == ("priority", info_hash, [0, 2], 0)
    # Select call: indices [1, 3] at priority 1
    assert fake.calls[1] == ("priority", info_hash, [1, 3], 1)
    # Resume on the same hash
    assert fake.calls[2] == ("resume", info_hash)


@pytest.mark.asyncio
async def test_qbit_no_save_path_omitted(monkeypatch, tmp_path):
    from app.core import qbit_client as qmod

    fake = _FakeQbitClient()
    monkeypatch.setattr(qmod.qbittorrentapi, "Client", lambda *a, **kw: fake)

    client = qmod.QbitClient(qmod.QbitConfig())
    await client.connect()

    fake_torrent = tmp_path / "x.torrent"
    fake_torrent.write_bytes(b"")

    await client.add_torrent_selective(
        torrent_path=str(fake_torrent),
        info_hash="b" * 40,
        all_indices=[0, 1],
        selected_indices=[0],
        save_path=None,  # use qBittorrent default
        wait_seconds=2.0,
        poll_interval=0.05,
    )

    assert "save_path" not in fake.added[0]


# ── Tree filter helper ────────────────────────────────────────────────────

class TestNodeMatchesFilter:
    """Tests for _node_matches_filter in torrent_select."""

    def setup_method(self):
        from app.tui.screens.torrent_select import _node_matches_filter
        self._matches = _node_matches_filter

    def test_matches_own_name(self, decoded: DecodedTorrent):
        readme = decoded.tree.children["readme.txt"]
        assert self._matches(readme, "readme")

    def test_matches_descendant(self, decoded: DecodedTorrent):
        # disc1 contains iso.cue; query "cue" should match disc1 via descendant
        disc1 = decoded.tree.children["disc1"]
        assert self._matches(disc1, "cue")

    def test_no_match_returns_false(self, decoded: DecodedTorrent):
        readme = decoded.tree.children["readme.txt"]
        assert not self._matches(readme, "zzz_no_such")

    def test_case_insensitive(self, decoded: DecodedTorrent):
        readme = decoded.tree.children["readme.txt"]
        assert self._matches(readme, "README")
        assert self._matches(readme, "ReadMe")

    def test_partial_match(self, decoded: DecodedTorrent):
        disc1 = decoded.tree.children["disc1"]
        assert self._matches(disc1, "disc")

    def test_no_match_in_folder_returns_false(self, decoded: DecodedTorrent):
        extras = decoded.tree.children["extras"]
        assert not self._matches(extras, "iso")

    def test_root_tree_matches_via_any_descendant(self, decoded: DecodedTorrent):
        assert self._matches(decoded.tree, "manual")

    def test_empty_query_always_matches(self, decoded: DecodedTorrent):
        # Every name contains ""
        for child in decoded.tree.children.values():
            assert self._matches(child, "")

