"""User settings + connection profile persistence."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_config_dir, user_log_dir

from app.core.constants import APP_NAME
from app.models.connection import ConnectionProfile

log = logging.getLogger(__name__)

SETTINGS_VERSION = 1


def config_dir() -> Path:
    p = Path(user_config_dir(APP_NAME))
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = Path(user_cache_dir(APP_NAME))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = Path(user_log_dir(APP_NAME))
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return config_dir() / "settings.json"


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME


@dataclass
class UsbSettings:
    auto_detect: bool = True
    manual_path: str | None = None


@dataclass
class Settings:
    version: int = SETTINGS_VERSION
    theme: str = "dark"
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    db_cache_max_age_hours: int = 24
    connections: list[ConnectionProfile] = field(default_factory=list)
    usb: UsbSettings = field(default_factory=UsbSettings)
    last_db_fetch: str | None = None
    aurora_path: str = "Hdd:\\Aurora\\"
    game_paths: list[str] = field(default_factory=list)
    game_scan_depth: int = 4
    local_god_path: str = ""
    game_install_path: str = "Hdd:\\Content\\0000000000000000\\"
    local_iso_path: str = ""
    torrent_download_folder: str = ""
    qbit_host: str = "localhost"
    qbit_port: int = 8080
    qbit_username: str = "admin"
    qbit_password: str = "adminadmin"
    auto_update: bool = True
    update_channel: str = "latest"  # 'latest' | 'pre-release'
    backup_dir: str = ""
    # Individual local content source paths (empty = use default Local*/ folder in app directory)
    local_mods_path: str = ""
    local_trainers_path: str = ""
    local_homebrew_path: str = ""
    local_game_saves_path: str = ""
    local_patches_path: str = ""
    local_cheats_path: str = ""
    local_title_updates_path: str = ""
    # Torrent watch folder (overrides bundled Torrent/ in repo root)
    torrent_folder: str = ""
    # Console install path overrides for local content (empty = use DB defaults)
    mod_install_path: str = ""
    trainer_install_path: str = ""
    homebrew_install_path: str = ""
    game_save_install_path: str = ""
    cheat_install_path: str = ""
    patch_install_path: str = ""
    title_update_install_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "theme": self.theme,
            "download_dir": self.download_dir,
            "db_cache_max_age_hours": self.db_cache_max_age_hours,
            "connections": [c.to_dict() for c in self.connections],
            "usb": {"auto_detect": self.usb.auto_detect, "manual_path": self.usb.manual_path},
            "last_db_fetch": self.last_db_fetch,
            "aurora_path": self.aurora_path,
            "game_paths": self.game_paths,
            "game_scan_depth": self.game_scan_depth,
            "local_god_path": self.local_god_path,
            "game_install_path": self.game_install_path,
            "local_iso_path": self.local_iso_path,
            "torrent_download_folder": self.torrent_download_folder,
            "qbit_host": self.qbit_host,
            "qbit_port": self.qbit_port,
            "qbit_username": self.qbit_username,
            "qbit_password": self.qbit_password,
            "auto_update": self.auto_update,
            "update_channel": self.update_channel,
            "backup_dir": self.backup_dir,
            "local_mods_path": self.local_mods_path,
            "local_trainers_path": self.local_trainers_path,
            "local_homebrew_path": self.local_homebrew_path,
            "local_game_saves_path": self.local_game_saves_path,
            "local_patches_path": self.local_patches_path,
            "local_cheats_path": self.local_cheats_path,
            "local_title_updates_path": self.local_title_updates_path,
            "torrent_folder": self.torrent_folder,
            "mod_install_path": self.mod_install_path,
            "trainer_install_path": self.trainer_install_path,
            "homebrew_install_path": self.homebrew_install_path,
            "game_save_install_path": self.game_save_install_path,
            "cheat_install_path": self.cheat_install_path,
            "patch_install_path": self.patch_install_path,
            "title_update_install_path": self.title_update_install_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Settings":
        usb_d = d.get("usb") or {}
        return cls(
            version=int(d.get("version", SETTINGS_VERSION)),
            theme=d.get("theme", "dark"),
            download_dir=d.get("download_dir") or str(default_download_dir()),
            db_cache_max_age_hours=int(d.get("db_cache_max_age_hours", 24)),
            connections=[ConnectionProfile.from_dict(c) for c in (d.get("connections") or [])],
            usb=UsbSettings(
                auto_detect=bool(usb_d.get("auto_detect", True)),
                manual_path=usb_d.get("manual_path"),
            ),
            last_db_fetch=d.get("last_db_fetch"),
            aurora_path=d.get("aurora_path", "Hdd:\\Aurora\\"),
            game_paths=list(d.get("game_paths") or []),
            game_scan_depth=int(d.get("game_scan_depth") or 4),
            local_god_path=d.get("local_god_path", ""),
            game_install_path=d.get("game_install_path", "Hdd:\\Content\\0000000000000000\\"),
            local_iso_path=d.get("local_iso_path", ""),
            torrent_download_folder=d.get("torrent_download_folder", ""),
            qbit_host=d.get("qbit_host", "localhost"),
            qbit_port=int(d.get("qbit_port", 8080)),
            qbit_username=d.get("qbit_username", "admin"),
            qbit_password=d.get("qbit_password", "adminadmin"),
            auto_update=bool(d.get("auto_update", True)),
            update_channel=d.get("update_channel", "latest"),
            backup_dir=d.get("backup_dir", ""),
            local_mods_path=d.get("local_mods_path", ""),
            local_trainers_path=d.get("local_trainers_path", ""),
            local_homebrew_path=d.get("local_homebrew_path", ""),
            local_game_saves_path=d.get("local_game_saves_path", ""),
            local_patches_path=d.get("local_patches_path", ""),
            local_cheats_path=d.get("local_cheats_path", ""),
            local_title_updates_path=d.get("local_title_updates_path", ""),
            torrent_folder=d.get("torrent_folder", ""),
            mod_install_path=d.get("mod_install_path", ""),
            trainer_install_path=d.get("trainer_install_path", ""),
            homebrew_install_path=d.get("homebrew_install_path", ""),
            game_save_install_path=d.get("game_save_install_path", ""),
            cheat_install_path=d.get("cheat_install_path", ""),
            patch_install_path=d.get("patch_install_path", ""),
            title_update_install_path=d.get("title_update_install_path", ""),
        )

    # --- Profile mgmt ---
    def default_profile(self) -> ConnectionProfile | None:
        for c in self.connections:
            if c.is_default:
                return c
        return self.connections[0] if self.connections else None

    def add_profile(self, profile: ConnectionProfile) -> None:
        if profile.is_default:
            for c in self.connections:
                c.is_default = False
        self.connections.append(profile)

    def update_profile(self, profile: ConnectionProfile) -> None:
        for i, c in enumerate(self.connections):
            if c.id == profile.id:
                if profile.is_default:
                    for o in self.connections:
                        o.is_default = False
                self.connections[i] = profile
                return
        self.add_profile(profile)

    def delete_profile(self, profile_id: str) -> None:
        self.connections = [c for c in self.connections if c.id != profile_id]

    def mark_db_fetched(self) -> None:
        self.last_db_fetch = datetime.now(timezone.utc).isoformat()


def load_settings() -> Settings:
    path = settings_path()
    if not path.exists():
        s = Settings()
        save_settings(s)
        return s
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return Settings.from_dict(data)
    except Exception:
        log.exception("Failed to load settings; using defaults")
        return Settings()


def save_settings(s: Settings) -> None:
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)
    tmp.replace(path)
