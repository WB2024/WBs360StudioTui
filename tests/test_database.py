import json
from pathlib import Path

import pytest

from app.core.database import DatabaseManager


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def write(p: Path, name: str, data) -> None:
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_text(json.dumps(data), encoding="utf-8")


def test_game_saves_filtered_to_xbox360(cache: Path):
    write(cache, "game-saves.json", [
        {"Id": 1, "Platform": "PS3", "Name": "ps3 save", "CategoryId": "x"},
        {"Id": 2, "Platform": "XBOX", "Name": "xbox save", "CategoryId": "x"},
        {"Id": 3, "Platform": "Xbox360", "Name": "xbox 2", "CategoryId": "x"},
        {"Id": 4, "Platform": "ps4", "Name": "skip", "CategoryId": "x"},
    ])
    # Required for has_cache check
    for f in ["categories.json", "game-mods.json", "homebrew.json", "trainers.json"]:
        write(cache, f, [])

    db = DatabaseManager(cache_root=cache)
    db.load_all()

    names = {s.name for s in db.game_saves}
    assert names == {"xbox save", "xbox 2"}


def test_mod_filtering(cache: Path):
    write(cache, "game-mods.json", [
        {"Id": 1, "Name": "Halo Mod", "CategoryId": "halo3", "ModType": "Multiplayer", "Region": "ALL"},
        {"Id": 2, "Name": "CoD Mod", "CategoryId": "cod", "ModType": "Singleplayer", "Region": "USA"},
    ])
    write(cache, "categories.json", {"Categories": [
        {"Id": "halo3", "Title": "Halo 3", "Type": "game"},
        {"Id": "cod", "Title": "Call of Duty", "Type": "game"},
    ]})
    for f in ["homebrew.json", "trainers.json", "game-saves.json"]:
        write(cache, f, [])

    db = DatabaseManager(cache_root=cache)
    db.load_all()

    assert len(db.get_game_mods(name="halo")) == 1
    assert len(db.get_game_mods(category_id="cod")) == 1
    assert len(db.get_game_mods(mod_type="single")) == 1
    assert len(db.get_game_mods(name="duty")) == 1  # matches category title


def test_resolve_category_name(cache: Path):
    write(cache, "categories.json", {"Categories": [{"Id": "halo3", "Title": "Halo 3", "Type": "game"}]})
    for f in ["game-mods.json", "homebrew.json", "trainers.json", "game-saves.json"]:
        write(cache, f, [])
    db = DatabaseManager(cache_root=cache)
    db.load_all()
    assert db.resolve_category_name("halo3") == "Halo 3"
    assert db.resolve_category_name("HALO3") == "Halo 3"
    assert db.resolve_category_name("missing") == "missing"
