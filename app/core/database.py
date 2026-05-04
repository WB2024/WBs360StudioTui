"""Database manager: fetch, cache, parse, query."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import cache_dir
from app.core import constants as C
from app.core.library_scanner import load_csv_titles
from app.core.local_library import (
    load_local_game_saves,
    load_local_homebrew,
    load_local_mods,
    load_local_trainers,
)
from app.core.downloader import DownloadError, Downloader
from app.models.category import CategoryItem
from app.models.game_cheat import GameCheatsData
from app.models.game_patch import GamePatchItemData, PatchEntry
from app.models.game_save import GameSaveItemData
from app.models.mod_item import ModItemData
from app.models.trainer import TrainerGameItem

log = logging.getLogger(__name__)


CACHE_FILES = {
    "categories": "categories.json",
    "game_mods": "game-mods.json",
    "homebrew": "homebrew.json",
    "trainers": "trainers.json",
    "game_cheats": "game-cheats.json",
    "game_saves": "game-saves.json",
    "title_ids": "titleids.json",
    "game_patches_zip": "game-patches.zip",
}


def _ci_eq(a: str, b: str) -> bool:
    return (a or "").lower() == (b or "").lower()


def _ci_in(needle: str, hay: str) -> bool:
    return (needle or "").lower() in (hay or "").lower()


class DatabaseManager:
    def __init__(self, downloader: Optional[Downloader] = None, cache_root: Optional[Path] = None) -> None:
        self.downloader = downloader or Downloader()
        self.cache_root = Path(cache_root) if cache_root else cache_dir()
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self.categories: list[CategoryItem] = []
        self.game_mods: list[ModItemData] = []
        self.homebrew: list[ModItemData] = []
        self.trainers: list[TrainerGameItem] = []
        self.game_cheats: list[GameCheatsData] = []
        self.game_saves: list[GameSaveItemData] = []
        self.title_ids: dict[str, str] = {}
        self.game_patches: list[GamePatchItemData] = []
        # Local content (from Local* repo folders)
        self.local_trainers: list[TrainerGameItem] = []
        self.local_mods: list[ModItemData] = []
        self.local_homebrew: list[ModItemData] = []
        self.local_game_saves: list[GameSaveItemData] = []
        # CSV title lookup supplementing the Arisen title_ids.json.
        self.csv_titles: dict[str, str] = self._load_csv_titles()

        self.last_fetch: Optional[datetime] = None

    # --- Paths ---
    def _path(self, key: str) -> Path:
        return self.cache_root / CACHE_FILES[key]

    def _patches_dir(self) -> Path:
        return self.cache_root / "game-patches"

    # --- Status ---
    async def check_status(self) -> bool:
        try:
            await self.downloader.fetch_text(C.STATUS_CHECK)
            return True
        except DownloadError:
            return False

    # --- Fetch ---
    async def fetch_all(self, progress: Optional[Any] = None) -> None:
        """Fetch every JSON + the patches ZIP. Save to cache."""
        targets = [
            ("categories", C.CATEGORIES_DATA),
            ("game_mods", C.GAME_MODS_XBOX),
            ("homebrew", C.HOMEBREW_XBOX),
            ("trainers", C.TRAINERS_XBOX),
            ("game_cheats", C.GAME_CHEATS_XBOX),
            ("game_saves", C.GAME_SAVES),
            ("title_ids", C.TITLE_IDS_XBOX),
        ]

        async def fetch_one(key: str, url: str) -> None:
            if progress:
                try:
                    progress(f"Fetching {key}...")
                except Exception:
                    pass
            text = await self.downloader.fetch_text(url)
            self._path(key).write_text(text, encoding="utf-8")

        # JSON in parallel
        await asyncio.gather(*(fetch_one(k, u) for k, u in targets))

        # Patches ZIP
        if progress:
            try:
                progress("Fetching game patches...")
            except Exception:
                pass
        await self.downloader.download(C.GAME_PATCHES_XBOX, self._path("game_patches_zip"))
        self._extract_patches_zip()

        self.last_fetch = datetime.now(timezone.utc)

    def _extract_patches_zip(self) -> None:
        zpath = self._path("game_patches_zip")
        if not zpath.exists():
            return
        out = self._patches_dir()
        if out.exists():
            for f in out.glob("**/*"):
                if f.is_file():
                    try:
                        f.unlink()
                    except OSError:
                        pass
        out.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(out)
        except zipfile.BadZipFile:
            log.warning("Patches ZIP is not a valid zip file")

    # --- Cache info ---
    def has_cache(self) -> bool:
        return all(self._path(k).exists() for k in ["categories", "game_mods", "homebrew", "trainers", "game_saves"])

    def cache_age_hours(self) -> Optional[float]:
        p = self._path("categories")
        if not p.exists():
            return None
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0

    # --- Load from cache ---
    def load_all(self, settings: Optional[Any] = None) -> None:
        self.categories = self._load_categories()
        self.game_mods = self._load_mod_list("game_mods")
        self.homebrew = self._load_mod_list("homebrew")
        self.trainers = self._load_trainers()
        self.game_cheats = self._load_cheats()
        self.game_saves = self._load_game_saves()
        self.title_ids = self._load_title_ids()
        self.game_patches = self._load_patches()
        # Local content
        self.local_trainers = load_local_trainers(
            source_path=getattr(settings, "local_trainers_path", "") or None,
            install_path_override=getattr(settings, "trainer_install_path", ""),
        )
        self.local_mods = load_local_mods(
            source_path=getattr(settings, "local_mods_path", "") or None,
            install_path_override=getattr(settings, "mod_install_path", ""),
        )
        self.local_homebrew = load_local_homebrew(
            source_path=getattr(settings, "local_homebrew_path", "") or None,
            install_path_override=getattr(settings, "homebrew_install_path", ""),
        )
        self.local_game_saves = load_local_game_saves(
            source_path=getattr(settings, "local_game_saves_path", "") or None,
            install_path_override=getattr(settings, "game_save_install_path", ""),
        )

    def _load_json(self, key: str) -> Any:
        p = self._path(key)
        if not p.exists():
            return None
        # utf-8-sig tolerates a BOM (Arisen Studio JSONs sometimes have one)
        with p.open("r", encoding="utf-8-sig") as f:
            return json.load(f)

    def _load_categories(self) -> list[CategoryItem]:
        data = self._load_json("categories")
        if not data:
            return []
        # Either {Categories: [...]} or [...] directly
        items = data.get("Categories") if isinstance(data, dict) else data
        return [CategoryItem.from_json(x) for x in (items or [])]

    def _load_csv_titles(self) -> dict[str, str]:
        """Load bundled gamelist_xbox360.csv from project root (if present)."""
        csv_path = Path(__file__).parent.parent.parent / "gamelist_xbox360.csv"
        return load_csv_titles(csv_path)

    def _load_mod_list(self, key: str) -> list[ModItemData]:
        data = self._load_json(key)
        if not data:
            return []
        items = data if isinstance(data, list) else data.get("Library") or data.get("Mods") or []
        return [ModItemData.from_json(x) for x in items]

    def _load_trainers(self) -> list[TrainerGameItem]:
        data = self._load_json("trainers")
        if not data:
            return []
        items = data if isinstance(data, list) else data.get("Library") or data.get("Trainers") or []
        return [TrainerGameItem.from_json(x) for x in items]

    def _load_cheats(self) -> list[GameCheatsData]:
        data = self._load_json("game_cheats")
        if not data:
            return []
        items = (
            data if isinstance(data, list)
            else data.get("GameCheats") or data.get("Library") or data.get("Cheats") or []
        )
        return [GameCheatsData.from_json(x) for x in items]

    def _load_game_saves(self) -> list[GameSaveItemData]:
        data = self._load_json("game_saves")
        if not data:
            return []
        items = data if isinstance(data, list) else data.get("Library") or data.get("GameSaves") or []
        all_saves = [GameSaveItemData.from_json(x) for x in items]
        # FILTER: Xbox 360 only
        return [s for s in all_saves if (s.platform or "").upper() in C.XBOX_PLATFORM_ALIASES]

    def _load_title_ids(self) -> dict[str, str]:
        data = self._load_json("title_ids")
        if not data:
            return {}
        # Possible shapes: {TitleId: Name}, [{TitleId, GameTitle}], {Games: [...]}
        if isinstance(data, dict) and "Games" in data and isinstance(data["Games"], list):
            entries = data["Games"]
        elif isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        else:
            return {}
        out: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("TitleId") or entry.get("title_id") or entry.get("Id")
            name = (
                entry.get("GameTitle") or entry.get("Name")
                or entry.get("Title") or entry.get("title")
            )
            if tid and name:
                out[str(tid)] = str(name)
        return out

    def _load_patches(self) -> list[GamePatchItemData]:
        out: list[GamePatchItemData] = []
        d = self._patches_dir()
        if not d.exists():
            return out
        for f in d.glob("**/*"):
            if not f.is_file():
                continue
            suffix = f.suffix.lower()
            if suffix not in (".json", ".yml", ".yaml", ".toml"):
                continue
            try:
                if suffix == ".json":
                    with f.open("r", encoding="utf-8-sig") as fh:
                        data = json.load(fh)
                    items = data if isinstance(data, list) else [data]
                    for entry in items:
                        if isinstance(entry, dict):
                            p = GamePatchItemData.from_json(entry)
                            p.source_file = f.name
                            out.append(p)
                else:
                    # Filename pattern: "TITLEID - Game Name.patch.toml"
                    stem = f.name
                    for trim in (".patch.toml", ".patch.yml", ".patch.yaml", ".toml", ".yml", ".yaml"):
                        if stem.lower().endswith(trim):
                            stem = stem[: -len(trim)]
                            break
                    title_id = ""
                    title_name = stem
                    if " - " in stem:
                        title_id, title_name = stem.split(" - ", 1)
                    p = GamePatchItemData(
                        title_id=title_id.strip(),
                        title_name=title_name.strip(),
                        source_file=f.name,
                    )
                    out.append(p)
            except Exception:
                log.exception("Failed parsing patch file %s", f)
        return out

    # --- Resolvers ---
    def resolve_category_name(self, category_id: str) -> str:
        for c in self.categories:
            if _ci_eq(c.id, category_id):
                return c.title
        return category_id or "Unknown"

    def resolve_game_title(self, title_id: str) -> str:
        if not title_id:
            return ""
        tid_up = title_id.upper()
        return (
            self.title_ids.get(title_id)
            or self.title_ids.get(tid_up)
            or self.csv_titles.get(tid_up)
            or title_id
        )

    def library_category_ids(self, library: dict[str, str]) -> set[str]:
        """Return category IDs whose resolved title matches a library game name.

        Used to filter mods/saves (which use category_id) when library_only is on.
        """
        if not library:
            return set()
        lib_names = {self.resolve_game_title(tid).lower() for tid in library}
        return {c.id for c in self.categories if c.title.lower() in lib_names}

    # --- Filters ---
    def get_game_mods(self, *, category_id: str = "", name: str = "", mod_type: str = "", region: str = "", source: str = "all") -> list[ModItemData]:
        pool = self._source_pool(self.game_mods, self.local_mods, source)
        return self._filter_mods(pool, category_id, name, mod_type, region)

    def get_homebrew(self, *, category_id: str = "", name: str = "", source: str = "all") -> list[ModItemData]:
        pool = self._source_pool(self.homebrew, self.local_homebrew, source)
        return self._filter_mods(pool, category_id, name, "", "")

    def _source_pool(self, online: list, local: list, source: str) -> list:
        """Merge online/local lists based on source filter."""
        if source == "online":
            return online
        if source == "local":
            return local
        return list(online) + list(local)

    def _source_pool_trainers(self, source: str) -> list[TrainerGameItem]:
        """Merge trainer lists; combine entries for the same title ID."""
        if source == "online":
            return self.trainers
        if source == "local":
            return self.local_trainers
        # "all" — merge by title_id so each game shows as a single row
        merged: dict[str, TrainerGameItem] = {}
        for g in self.trainers:
            merged[g.title_id.upper()] = TrainerGameItem(
                title_id=g.title_id,
                description=g.description,
                trainers=list(g.trainers),
            )
        for g in self.local_trainers:
            key = g.title_id.upper()
            if key in merged:
                merged[key].trainers.extend(g.trainers)
            else:
                merged[key] = TrainerGameItem(
                    title_id=g.title_id,
                    description=g.description,
                    trainers=list(g.trainers),
                )
        return list(merged.values())

    def _filter_mods(
        self,
        items: Iterable[ModItemData],
        category_id: str,
        name: str,
        mod_type: str,
        region: str,
    ) -> list[ModItemData]:
        out = []
        for m in items:
            if category_id and not _ci_eq(m.category_id, category_id):
                continue
            if name:
                cat_title = self.resolve_category_name(m.category_id)
                if not (_ci_in(name, m.name) or _ci_in(name, cat_title)):
                    continue
            if mod_type and not _ci_in(mod_type, m.mod_type):
                continue
            if region and region.upper() not in ("ALL", "") and not _ci_in(region, m.region):
                continue
            out.append(m)
        return out

    def get_trainers(self, *, title_id: str = "", name: str = "", source: str = "all") -> list[TrainerGameItem]:
        pool = self._source_pool_trainers(source)
        out = []
        for g in pool:
            if title_id and not _ci_eq(g.title_id, title_id):
                continue
            if name:
                game_title = self.resolve_game_title(g.title_id)
                match = _ci_in(name, g.title_id) or _ci_in(name, game_title) or any(_ci_in(name, t.name) for t in g.trainers)
                if not match:
                    continue
            out.append(g)
        return out

    def get_game_saves(self, *, category_id: str = "", name: str = "", region: str = "", source: str = "all") -> list[GameSaveItemData]:
        pool = self._source_pool(self.game_saves, self.local_game_saves, source)
        out = []
        for s in pool:
            if category_id and not _ci_eq(s.category_id, category_id):
                continue
            if name:
                cat_title = self.resolve_category_name(s.category_id)
                if not (_ci_in(name, s.name) or _ci_in(name, cat_title)):
                    continue
            if region and region.upper() not in ("ALL", "") and not _ci_in(region, s.region):
                continue
            out.append(s)
        return out

    def get_game_cheats(self, *, name: str = "") -> list[GameCheatsData]:
        if not name:
            return list(self.game_cheats)
        return [c for c in self.game_cheats if _ci_in(name, c.game) or any(_ci_in(name, ch.name) for ch in c.cheats)]

    def get_game_patches(self, *, name: str = "") -> list[GamePatchItemData]:
        if not name:
            return list(self.game_patches)
        return [
            p for p in self.game_patches
            if _ci_in(name, p.title_name) or _ci_in(name, p.title_id) or any(_ci_in(name, e.name) for e in p.patches)
        ]

    def get_categories(self) -> list[CategoryItem]:
        return list(self.categories)
