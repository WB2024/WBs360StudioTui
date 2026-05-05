"""Tests for CSV media-ID loading and TU compatibility logic.

Covers:
  - load_csv_media_ids() in library_scanner
  - DatabaseManager.get_known_media_ids()
  - DatabaseManager.check_tu_media_compatibility()
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.library_scanner import load_csv_media_ids, load_csv_titles
from app.core.database import DatabaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write a tab-separated gamelist_xbox360.csv to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("title_id\tmedia_id\ttitle_name\n")
        for r in rows:
            f.write(f"{r.get('title_id','')}\t{r.get('media_id','')}\t{r.get('title_name','')}\n")


def _minimal_db(cache: Path, csv_rows: list[dict]) -> DatabaseManager:
    """Return a DatabaseManager whose CSV loaders use a *temporary* CSV.

    The real gamelist_xbox360.csv in the project root is NEVER touched.
    We patch DatabaseManager._load_csv_titles and _load_csv_media_ids at
    the instance level after construction so the temp CSV is the only source.
    """
    cache.mkdir(parents=True, exist_ok=True)
    for stub in ("categories.json", "game-mods.json", "homebrew.json", "trainers.json", "game-saves.json"):
        (cache / stub).write_text(json.dumps([]), encoding="utf-8")
    (cache / "categories.json").write_text(json.dumps({"Categories": []}), encoding="utf-8")

    # Write our test rows into an isolated temp CSV inside the cache dir
    csv_path = cache / "test_gamelist.csv"
    _write_csv(csv_path, csv_rows)

    # Patch both loaders so DatabaseManager never reads the real project CSV
    with (
        patch.object(DatabaseManager, "_load_csv_titles", lambda self: load_csv_titles(csv_path)),
        patch.object(DatabaseManager, "_load_csv_media_ids", lambda self: load_csv_media_ids(csv_path)),
    ):
        db = DatabaseManager(cache_root=cache)

    return db


# ---------------------------------------------------------------------------
# load_csv_media_ids — unit tests (no DatabaseManager)
# ---------------------------------------------------------------------------

class TestLoadCsvMediaIds:
    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        result = load_csv_media_ids(tmp_path / "nonexistent.csv")
        assert result == {}

    def test_skips_rows_with_blank_media_id(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "AAAABBBB", "media_id": "", "title_name": "Game A"},
            {"title_id": "CCCCDDDD", "media_id": "   ", "title_name": "Game B"},
        ])
        result = load_csv_media_ids(csv_path)
        assert result == {}

    def test_skips_all_zero_media_id(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "AAAABBBB", "media_id": "00000000", "title_name": "Game A"},
        ])
        result = load_csv_media_ids(csv_path)
        assert result == {}

    def test_loads_valid_media_id(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "584111F7", "media_id": "7CD33B56", "title_name": "Minecraft"},
        ])
        result = load_csv_media_ids(csv_path)
        assert result == {"584111F7": ["7CD33B56"]}

    def test_normalises_title_and_media_id_to_uppercase(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "584111f7", "media_id": "7cd33b56", "title_name": "Minecraft"},
        ])
        result = load_csv_media_ids(csv_path)
        assert "584111F7" in result
        assert "7CD33B56" in result["584111F7"]

    def test_aggregates_multiple_regional_rows_per_title(self, tmp_path: Path) -> None:
        """Same Title ID → different Media IDs per disc region."""
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "415607E6", "media_id": "AABBCCDD", "title_name": "CoD4 USA"},
            {"title_id": "415607E6", "media_id": "11223344", "title_name": "CoD4 EUR"},
            {"title_id": "415607E6", "media_id": "DEADBEEF", "title_name": "CoD4 JPN"},
        ])
        result = load_csv_media_ids(csv_path)
        assert sorted(result["415607E6"]) == sorted(["AABBCCDD", "11223344", "DEADBEEF"])

    def test_deduplicates_identical_media_ids(self, tmp_path: Path) -> None:
        """Duplicate rows should not create duplicate entries in the list."""
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "415607E6", "media_id": "AABBCCDD", "title_name": "CoD4"},
            {"title_id": "415607E6", "media_id": "AABBCCDD", "title_name": "CoD4 duplicate"},
        ])
        result = load_csv_media_ids(csv_path)
        assert result["415607E6"].count("AABBCCDD") == 1

    def test_mixes_valid_and_empty_rows_same_title(self, tmp_path: Path) -> None:
        """Rows with blank media_id are excluded; valid row is retained."""
        csv_path = tmp_path / "games.csv"
        _write_csv(csv_path, [
            {"title_id": "415607E6", "media_id": "", "title_name": "CoD4 (no ID)"},
            {"title_id": "415607E6", "media_id": "AABBCCDD", "title_name": "CoD4 USA"},
        ])
        result = load_csv_media_ids(csv_path)
        assert result == {"415607E6": ["AABBCCDD"]}


# ---------------------------------------------------------------------------
# DatabaseManager.get_known_media_ids
# ---------------------------------------------------------------------------

class TestGetKnownMediaIds:
    def test_returns_list_for_known_title(self, tmp_path: Path) -> None:
        db = _minimal_db(
            tmp_path / "cache",
            [{"title_id": "584111F7", "media_id": "7CD33B56", "title_name": "Minecraft"}],
        )
        ids = db.get_known_media_ids("584111F7")
        assert ids == ["7CD33B56"]

    def test_case_insensitive_lookup(self, tmp_path: Path) -> None:
        db = _minimal_db(
            tmp_path / "cache",
            [{"title_id": "584111F7", "media_id": "7CD33B56", "title_name": "Minecraft"}],
        )
        assert db.get_known_media_ids("584111f7") == ["7CD33B56"]

    def test_returns_empty_list_for_unknown_title(self, tmp_path: Path) -> None:
        db = _minimal_db(tmp_path / "cache", [])
        assert db.get_known_media_ids("DEADBEEF") == []

    def test_returns_empty_list_for_blank_title_id(self, tmp_path: Path) -> None:
        db = _minimal_db(tmp_path / "cache", [])
        assert db.get_known_media_ids("") == []
        assert db.get_known_media_ids(None) == []  # type: ignore[arg-type]

    def test_returns_multiple_variants(self, tmp_path: Path) -> None:
        db = _minimal_db(
            tmp_path / "cache",
            [
                {"title_id": "415607E6", "media_id": "AABBCCDD", "title_name": "CoD4 USA"},
                {"title_id": "415607E6", "media_id": "11223344", "title_name": "CoD4 EUR"},
            ],
        )
        ids = db.get_known_media_ids("415607E6")
        assert sorted(ids) == ["11223344", "AABBCCDD"]


# ---------------------------------------------------------------------------
# DatabaseManager.check_tu_media_compatibility
# ---------------------------------------------------------------------------

class TestCheckTuMediaCompatibility:
    def _db_with_variants(self, tmp_path: Path, variants: list[str]) -> DatabaseManager:
        rows = [
            {"title_id": "415607E6", "media_id": mid, "title_name": f"CoD4 {mid}"}
            for mid in variants
        ]
        return _minimal_db(tmp_path / "cache", rows)

    def test_compatible_when_media_id_matches(self, tmp_path: Path) -> None:
        db = self._db_with_variants(tmp_path, ["AABBCCDD", "11223344"])
        assert db.check_tu_media_compatibility("AABBCCDD", "415607E6") == "compatible"

    def test_compatible_case_insensitive(self, tmp_path: Path) -> None:
        db = self._db_with_variants(tmp_path, ["AABBCCDD"])
        assert db.check_tu_media_compatibility("aabbccdd", "415607E6") == "compatible"

    def test_incompatible_when_media_id_not_in_list(self, tmp_path: Path) -> None:
        db = self._db_with_variants(tmp_path, ["AABBCCDD", "11223344"])
        assert db.check_tu_media_compatibility("DEADBEEF", "415607E6") == "incompatible"

    def test_unknown_when_tu_media_id_is_empty(self, tmp_path: Path) -> None:
        db = self._db_with_variants(tmp_path, ["AABBCCDD"])
        assert db.check_tu_media_compatibility("", "415607E6") == "unknown"

    def test_unknown_when_tu_media_id_is_all_zeros(self, tmp_path: Path) -> None:
        db = self._db_with_variants(tmp_path, ["AABBCCDD"])
        assert db.check_tu_media_compatibility("00000000", "415607E6") == "unknown"

    def test_unknown_when_no_csv_data_for_title(self, tmp_path: Path) -> None:
        """Game not in CSV → can't compare → unknown (not incompatible)."""
        db = _minimal_db(tmp_path / "cache", [])
        assert db.check_tu_media_compatibility("AABBCCDD", "NOTINTHERE") == "unknown"

    def test_unknown_when_all_csv_rows_have_blank_media_id(self, tmp_path: Path) -> None:
        """Every CSV row for this title has a blank Media ID → still unknown."""
        db = _minimal_db(
            tmp_path / "cache",
            [{"title_id": "415607E6", "media_id": "", "title_name": "CoD4 (no ID)"}],
        )
        assert db.check_tu_media_compatibility("AABBCCDD", "415607E6") == "unknown"

    def test_unknown_when_title_id_is_blank(self, tmp_path: Path) -> None:
        db = _minimal_db(tmp_path / "cache", [])
        assert db.check_tu_media_compatibility("AABBCCDD", "") == "unknown"

    def test_second_variant_also_compatible(self, tmp_path: Path) -> None:
        """Any variant in the list should satisfy compatibility."""
        db = self._db_with_variants(tmp_path, ["AABBCCDD", "11223344"])
        assert db.check_tu_media_compatibility("11223344", "415607E6") == "compatible"
