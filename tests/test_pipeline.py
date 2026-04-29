"""Tests for app.core.pipeline — scan_download_folder and local_god_rename."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core.pipeline import (
    DiscoveredGod,
    DiscoveredIso,
    GameStatus,
    PipelineGame,
    _find_isos_in_dir,
    scan_download_folder,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_iso(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)
    return path


def _make_god(base: Path, name: str, tid: str, ct: str = "00007000") -> Path:
    """Create a minimal GOD directory structure."""
    god_root = base / name / tid / ct
    god_root.mkdir(parents=True, exist_ok=True)
    # Minimal CON container file (no extension)
    container = god_root / "DEADBEEF01234567"
    container.write_bytes(b"\x43\x4F\x4E")  # "CON" magic bytes
    return god_root


# ── scan_download_folder ───────────────────────────────────────────────────────

class TestScanDownloadFolder:
    def test_missing_folder_returns_empty(self, tmp_path):
        result = scan_download_folder(tmp_path / "nonexistent")
        assert result == []

    def test_flat_iso_in_root(self, tmp_path):
        _make_iso(tmp_path / "DeadRising.iso")
        result = scan_download_folder(tmp_path)
        assert len(result) == 1
        g = result[0]
        assert g.iso is not None
        assert g.name == "DeadRising"
        assert g.iso.disc_label == ""
        assert g.display_type == "ISO"

    def test_iso_in_subfolder(self, tmp_path):
        _make_iso(tmp_path / "Dead Rising" / "game.iso")
        result = scan_download_folder(tmp_path)
        assert len(result) == 1
        g = result[0]
        assert g.iso is not None
        assert g.name == "Dead Rising"
        assert g.display_type == "ISO"

    def test_multi_disc_isos_in_subfolder(self, tmp_path):
        _make_iso(tmp_path / "SplinterCell" / "disc1.iso")
        _make_iso(tmp_path / "SplinterCell" / "disc2.iso")
        result = scan_download_folder(tmp_path)
        assert len(result) == 2
        labels = {g.iso.disc_label for g in result}
        assert "disc1" in labels
        assert "disc2" in labels
        # Both should have the same game name
        assert all(g.name == "SplinterCell" for g in result)

    def test_god_container_detected(self, tmp_path):
        _make_god(tmp_path, "HaloReach", "4D5307E6")
        result = scan_download_folder(tmp_path)
        assert len(result) == 1
        g = result[0]
        assert g.god is not None
        assert g.is_god_source
        assert g.display_type == "GOD"

    def test_mixed_iso_and_god(self, tmp_path):
        _make_iso(tmp_path / "GameA.iso")
        _make_god(tmp_path, "GameB", "4D5307E7")
        result = scan_download_folder(tmp_path)
        assert len(result) == 2
        iso_games = [g for g in result if g.iso]
        god_games = [g for g in result if g.god]
        assert len(iso_games) == 1
        assert len(god_games) == 1

    def test_empty_folder_returns_empty(self, tmp_path):
        result = scan_download_folder(tmp_path)
        assert result == []

    def test_non_iso_files_ignored(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "image.jpg").write_bytes(b"\xff\xd8")
        result = scan_download_folder(tmp_path)
        assert result == []

    def test_case_insensitive_iso_extension(self, tmp_path):
        _make_iso(tmp_path / "Game.ISO")
        result = scan_download_folder(tmp_path)
        assert len(result) == 1


# ── _find_isos_in_dir ──────────────────────────────────────────────────────────

class TestFindIsosInDir:
    def test_single_iso_no_label(self, tmp_path):
        folder = tmp_path / "Game"
        _make_iso(folder / "game.iso")
        result = _find_isos_in_dir(folder)
        assert len(result) == 1
        path, label = result[0]
        assert label == ""

    def test_multi_disc_extracts_label(self, tmp_path):
        folder = tmp_path / "Game"
        _make_iso(folder / "disc1.iso")
        _make_iso(folder / "disc2.iso")
        result = _find_isos_in_dir(folder)
        assert len(result) == 2
        labels = {label for _, label in result}
        assert "disc1" in labels
        assert "disc2" in labels

    def test_disc_with_space_normalised(self, tmp_path):
        folder = tmp_path / "Game"
        _make_iso(folder / "Game Disc 1.iso")
        _make_iso(folder / "Game Disc 2.iso")
        result = _find_isos_in_dir(folder)
        labels = {label for _, label in result}
        assert any("disc" in lab.lower() for lab in labels)


# ── PipelineGame ──────────────────────────────────────────────────────────────

class TestPipelineGame:
    def test_initial_status_pending(self):
        g = PipelineGame(name="TestGame")
        assert g.status == GameStatus.PENDING

    def test_is_god_source_false_for_iso(self, tmp_path):
        iso = DiscoveredIso(name="Game", iso_path=tmp_path / "a.iso", disc_label="")
        g = PipelineGame(name="Game", iso=iso)
        assert not g.is_god_source

    def test_is_god_source_true_for_god(self, tmp_path):
        from app.models.god_game import GodGameItem
        god = GodGameItem(
            name="Game", title_id="45410822", content_type="00007000",
            local_path=tmp_path, container_file="AABB",
        )
        g = PipelineGame(name="Game", god=god)
        assert g.is_god_source

    def test_display_type_iso(self, tmp_path):
        iso = DiscoveredIso(name="Game", iso_path=tmp_path / "a.iso", disc_label="")
        g = PipelineGame(name="Game", iso=iso)
        assert g.display_type == "ISO"

    def test_display_type_iso_with_disc_label(self, tmp_path):
        iso = DiscoveredIso(name="Game", iso_path=tmp_path / "a.iso", disc_label="disc1")
        g = PipelineGame(name="Game", iso=iso)
        assert "disc1" in g.display_type

    def test_display_type_god(self, tmp_path):
        from app.models.god_game import GodGameItem
        god = GodGameItem(
            name="Game", title_id="45410822", content_type="00007000",
            local_path=tmp_path, container_file="AABB",
        )
        g = PipelineGame(name="Game", god=god)
        assert g.display_type == "GOD"
