"""Scan Local* folders and build model objects for locally-stored content.

Directory conventions:
  LocalTrainers/{TitleID}/{TrainerFile}.xex   — one file per title ID folder
  LocalMods/{TitleID}/{filename}              — mod files per title ID
  LocalHomebrew/{AppName}/{filename}          — homebrew apps
  LocalGameSaves/{TitleID}/{filename}         — game save files
  LocalPatches/{TitleID}/{filename}           — patch files
  LocalCheats/{TitleID}/{filename}            — cheat files

For folders that hold a JSON metadata file (mod.json / save.json / etc.), that
file is parsed for name/description/author.  Otherwise a basic entry is built
from the file name and parent directory name.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from app.models.game_save import GameSaveItemData
from app.models.mod_item import DownloadFile, ModItemData
from app.models.trainer import TrainerGameItem, TrainerItem

log = logging.getLogger(__name__)

# Root of the repository / project (two levels up from this file)
_REPO_ROOT = Path(__file__).parent.parent.parent


def _local_dir(name: str) -> Path:
    return _REPO_ROOT / name


# ---------------------------------------------------------------------------
# Trainers
# ---------------------------------------------------------------------------

def _trainer_install_path(title_id: str, stem: str, filename: str) -> str:
    """Standard Aurora trainer install path for a local trainer file."""
    return f"{{AURORAPATH}}\\User\\Trainers\\{title_id}\\{stem}\\{filename}"


def load_local_trainers() -> list[TrainerGameItem]:
    """Scan LocalTrainers/ and build TrainerGameItem objects."""
    base = _local_dir("LocalTrainers")
    if not base.is_dir():
        return []

    results: list[TrainerGameItem] = []
    for tid_dir in sorted(base.iterdir()):
        if not tid_dir.is_dir():
            continue
        title_id = tid_dir.name.upper()
        trainers: list[TrainerItem] = []
        for f in sorted(tid_dir.iterdir()):
            if not f.is_file():
                continue
            stem = f.stem  # e.g. "Trainer(RETROBYTE)"
            install_path = _trainer_install_path(title_id, stem, f.name)
            trainers.append(TrainerItem(
                name=stem,
                type="aurora",  # RETROBYTE trainers run under Aurora
                url="",
                last_updated="",
                install_paths=[install_path],
                source="local",
                local_path=str(f.resolve()),
            ))
        if trainers:
            results.append(TrainerGameItem(
                title_id=title_id,
                description="Local trainer",
                trainers=trainers,
            ))

    log.info("Loaded %d local trainer title(s) from LocalTrainers/", len(results))
    return results


# ---------------------------------------------------------------------------
# Mods
# ---------------------------------------------------------------------------

def _read_json_meta(directory: Path, default_name: str) -> dict:
    """Try to read a mod.json / meta.json in the directory for metadata."""
    for name in ("mod.json", "meta.json", "info.json"):
        p = directory / name
        if p.is_file():
            try:
                with p.open("r", encoding="utf-8-sig") as fh:
                    return json.load(fh)
            except Exception:
                pass
    return {}


def _mod_files_from_dir(directory: Path, title_id: str) -> list[DownloadFile]:
    """Collect non-metadata files in a directory as DownloadFile objects."""
    skip = {"mod.json", "meta.json", "info.json"}
    files = []
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.name.lower() not in skip:
            files.append(DownloadFile(
                name=f.name,
                install_paths=[f"Hdd:\\JTAG\\{title_id}\\"],
                local_path=str(f.resolve()),
            ))
    return files


def load_local_mods() -> list[ModItemData]:
    """Scan LocalMods/ and build ModItemData objects."""
    base = _local_dir("LocalMods")
    if not base.is_dir():
        return []

    results: list[ModItemData] = []
    for tid_dir in sorted(base.iterdir()):
        if not tid_dir.is_dir():
            continue
        title_id = tid_dir.name.upper()
        meta = _read_json_meta(tid_dir, title_id)
        dl_files = _mod_files_from_dir(tid_dir, title_id)
        if not dl_files:
            continue
        results.append(ModItemData(
            category_id=title_id,
            name=meta.get("Name") or meta.get("name") or title_id,
            created_by=meta.get("Author") or meta.get("author") or "Unknown",
            version=str(meta.get("Version") or meta.get("version") or ""),
            description=meta.get("Description") or meta.get("description") or "",
            mod_type=meta.get("ModType") or meta.get("type") or "Local",
            download_files=dl_files,
            source="local",
        ))

    log.info("Loaded %d local mod(s) from LocalMods/", len(results))
    return results


# ---------------------------------------------------------------------------
# Homebrew
# ---------------------------------------------------------------------------

def load_local_homebrew() -> list[ModItemData]:
    """Scan LocalHomebrew/ and build ModItemData objects."""
    base = _local_dir("LocalHomebrew")
    if not base.is_dir():
        return []

    results: list[ModItemData] = []
    for app_dir in sorted(base.iterdir()):
        if not app_dir.is_dir():
            continue
        meta = _read_json_meta(app_dir, app_dir.name)
        dl_files = _mod_files_from_dir(app_dir, app_dir.name)
        if not dl_files:
            continue
        results.append(ModItemData(
            category_id=app_dir.name,
            name=meta.get("Name") or meta.get("name") or app_dir.name,
            created_by=meta.get("Author") or meta.get("author") or "Unknown",
            version=str(meta.get("Version") or meta.get("version") or ""),
            description=meta.get("Description") or meta.get("description") or "",
            mod_type="Homebrew",
            download_files=dl_files,
            source="local",
        ))

    log.info("Loaded %d local homebrew app(s) from LocalHomebrew/", len(results))
    return results


# ---------------------------------------------------------------------------
# Game Saves
# ---------------------------------------------------------------------------

def load_local_game_saves() -> list[GameSaveItemData]:
    """Scan LocalGameSaves/ and build GameSaveItemData objects."""
    base = _local_dir("LocalGameSaves")
    if not base.is_dir():
        return []

    results: list[GameSaveItemData] = []
    for tid_dir in sorted(base.iterdir()):
        if not tid_dir.is_dir():
            continue
        title_id = tid_dir.name.upper()
        meta = _read_json_meta(tid_dir, title_id)
        skip = {"mod.json", "meta.json", "info.json"}
        dl_files = []
        for f in sorted(tid_dir.iterdir()):
            if f.is_file() and f.name.lower() not in skip:
                dl_files.append(DownloadFile(
                    name=f.name,
                    install_paths=[f"Hdd:\\Content\\0000000000000000\\{title_id}\\000B0000\\"],
                    local_path=str(f.resolve()),
                ))
        if not dl_files:
            continue
        results.append(GameSaveItemData(
            category_id=title_id,
            name=meta.get("Name") or meta.get("name") or title_id,
            region=meta.get("Region") or meta.get("region") or "ALL",
            created_by=meta.get("Author") or meta.get("author") or "Unknown",
            version=str(meta.get("Version") or meta.get("version") or ""),
            description=meta.get("Description") or meta.get("description") or "",
            download_files=dl_files,
            platform="Xbox 360",
            source="local",
        ))

    log.info("Loaded %d local game save(s) from LocalGameSaves/", len(results))
    return results
