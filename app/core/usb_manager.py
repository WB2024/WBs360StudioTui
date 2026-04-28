"""USB drive detection + path mapping (Windows + Linux)."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.core.paths import map_xbox_path_to_usb

log = logging.getLogger(__name__)


@dataclass
class UsbDrive:
    label: str
    mount_point: str
    total_bytes: int
    free_bytes: int
    fs_type: str = ""

    @property
    def display(self) -> str:
        gb = self.total_bytes / (1024 ** 3) if self.total_bytes else 0
        return f"{self.label or self.mount_point} ({self.mount_point}) — {gb:.1f} GB"


class UsbManager:
    def detect_drives(self) -> list[UsbDrive]:
        drives: list[UsbDrive] = []
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            log.exception("psutil.disk_partitions failed")
            return drives

        for p in partitions:
            mount = p.mountpoint
            if not mount:
                continue
            opts = (p.opts or "").lower()
            is_removable = "removable" in opts or "rw,nosuid" in opts
            if sys.platform.startswith("win"):
                # On Windows opts contains "removable" for USB sticks
                if "removable" not in opts:
                    continue
            else:
                # Linux: only consider /media, /mnt, /run/media
                if not (mount.startswith("/media/") or mount.startswith("/mnt/") or mount.startswith("/run/media/")):
                    continue
            try:
                usage = psutil.disk_usage(mount)
            except OSError:
                continue
            label = Path(mount).name or mount
            drives.append(UsbDrive(
                label=label,
                mount_point=mount,
                total_bytes=usage.total,
                free_bytes=usage.free,
                fs_type=p.fstype or "",
            ))
        return drives

    def get_available_space(self, drive_path: str) -> int:
        try:
            return psutil.disk_usage(drive_path).free
        except OSError:
            return 0

    def map_xbox_path_to_usb(self, xbox_path: str, usb_root: str) -> str:
        return map_xbox_path_to_usb(xbox_path, usb_root)

    def copy_file(self, local_path: str | Path, dest_path: str | Path) -> None:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(local_path), str(dest))

    def ensure_directory(self, dest_dir: str | Path) -> None:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
