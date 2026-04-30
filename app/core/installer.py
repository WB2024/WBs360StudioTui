"""Install orchestration: download → extract → transfer (FTP or USB)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from app.core.downloader import Downloader
from app.core.ftp_client import FtpClient
from app.core.paths import (
    is_directory_path,
    join_xbox_path,
    map_xbox_path_to_usb,
    normalize_xbox_path,
)
from app.core.usb_manager import UsbManager
from app.models.game_save import GameSaveItemData
from app.models.god_game import GodGameItem
from app.models.mod_item import DownloadFile, ModItemData
from app.models.title_update import TitleUpdateItem
from app.models.trainer import TrainerItem

log = logging.getLogger(__name__)

# Generic progress callback: (stage_name: str, current: int, total: int)
StageProgress = Callable[[str, int, int], None]


def _resolve_aurora(paths: list[str], aurora_path: str) -> list[str]:
    """Replace {AURORAPATH} placeholder with the user's configured Aurora folder."""
    norm = aurora_path.replace("\\", "/")
    if not norm.endswith("/"):
        norm += "/"
    return [p.replace("{AURORAPATH}", norm) for p in paths]


@dataclass
class InstallResult:
    success: bool
    message: str = ""
    files_transferred: int = 0
    errors: list[str] = field(default_factory=list)


InstallableItem = Union[ModItemData, GameSaveItemData, TrainerItem]


def _get_download_files(item: InstallableItem) -> list[DownloadFile]:
    """Return DownloadFile-like list for any installable item."""
    if isinstance(item, TrainerItem):
        return [DownloadFile(
            name=item.name,
            url=item.url,
            install_paths=list(item.install_paths or []),
            local_path=item.local_path,
        )]
    return list(item.download_files or [])


def _get_item_name(item: InstallableItem) -> str:
    return getattr(item, "name", "") or "item"


def _is_zip(path: Path) -> bool:
    if path.suffix.lower() == ".zip":
        return True
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _resolve_remote_paths(install_paths: list[str], local_files: list[Path], temp_root: Path) -> list[tuple[Path, str]]:
    """
    Map local files → remote install paths.
    Rules:
      - If 1 install path and N files: all files go into that directory
        (preserving relative subpath when extracted from zip)
      - If multiple install paths and same count of files: 1-to-1 in order
      - If multiple install paths and different file count: each install path
        receives ALL files (treat as broadcast — common for trainers with multiple HDD locations)
    """
    out: list[tuple[Path, str]] = []
    if not install_paths:
        return out

    if len(install_paths) == 1:
        ip = install_paths[0]
        for lf in local_files:
            if is_directory_path(ip):
                # Preserve relative path within zip
                rel = lf.relative_to(temp_root) if temp_root in lf.parents or lf == temp_root else Path(lf.name)
                base = normalize_xbox_path(ip)
                if not base.endswith("/"):
                    base += "/"
                out.append((lf, base + str(rel).replace(os.sep, "/")))
            else:
                # Exact file destination — only meaningful for single-file installs
                out.append((lf, normalize_xbox_path(ip)))
        return out

    if len(install_paths) == len(local_files):
        for lf, ip in zip(local_files, install_paths):
            out.append((lf, join_xbox_path(ip, lf.name)))
        return out

    # Broadcast: every file to every install path
    for ip in install_paths:
        for lf in local_files:
            if is_directory_path(ip):
                rel = lf.relative_to(temp_root) if temp_root in lf.parents else Path(lf.name)
                base = normalize_xbox_path(ip)
                if not base.endswith("/"):
                    base += "/"
                out.append((lf, base + str(rel).replace(os.sep, "/")))
            else:
                out.append((lf, normalize_xbox_path(ip)))
    return out


async def _prepare_local_files(
    download_file: DownloadFile,
    downloader: Downloader,
    temp_root: Path,
    progress: Optional[StageProgress],
) -> tuple[Path, list[Path]]:
    """Download (and extract if zip). Return (extract_root, [local files]).

    If download_file.local_path is set the file is copied from disk directly,
    skipping any HTTP download.
    """
    # --- Local file (no download needed) ---
    if download_file.local_path:
        src = Path(download_file.local_path)
        if not src.is_file():
            raise FileNotFoundError(f"Local file not found: {src}")
        dest = temp_root / src.name
        shutil.copy2(src, dest)
        if _is_zip(dest):
            if progress:
                progress("extract", 0, 0)
            extract_dir = temp_root / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dest, "r") as zf:
                zf.extractall(extract_dir)
            files = [p for p in extract_dir.rglob("*") if p.is_file()]
            return extract_dir, files
        return temp_root, [dest]

    # --- Remote download ---
    name = download_file.name or "download.bin"
    local = temp_root / name

    def _dl_cb(cur: int, total: int) -> None:
        if progress:
            progress("download", cur, total)

    if progress:
        progress("download", 0, 0)
    await downloader.download(download_file.url, local, progress_callback=_dl_cb)

    if _is_zip(local):
        if progress:
            progress("extract", 0, 0)
        extract_dir = temp_root / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(local, "r") as zf:
            zf.extractall(extract_dir)
        files = [p for p in extract_dir.rglob("*") if p.is_file()]
        return extract_dir, files

    return temp_root, [local]


class Installer:
    def __init__(self, downloader: Optional[Downloader] = None) -> None:
        self.downloader = downloader or Downloader()

    async def install_via_ftp(
        self,
        item: InstallableItem,
        ftp_client: FtpClient,
        progress: Optional[StageProgress] = None,
        aurora_path: str = "Hdd:\\Aurora\\",
    ) -> InstallResult:
        result = InstallResult(success=False)
        files = _get_download_files(item)
        if not files:
            result.message = "No download files defined for this item"
            return result

        if not ftp_client.is_connected:
            try:
                await ftp_client.connect()
            except Exception as e:
                result.message = f"FTP connect failed: {e}"
                return result

        for df in files:
            tmp = Path(tempfile.mkdtemp(prefix="x360tm-"))
            try:
                root, local_files = await _prepare_local_files(df, self.downloader, tmp, progress)
                resolved_paths = _resolve_aurora(df.install_paths, aurora_path)
                mappings = _resolve_remote_paths(resolved_paths, local_files, root)
                if progress:
                    progress("transfer", 0, len(mappings))
                for idx, (lf, remote) in enumerate(mappings, 1):
                    try:
                        await ftp_client.upload_file(lf, remote)
                        result.files_transferred += 1
                    except Exception as e:
                        msg = f"Failed uploading {lf.name} → {remote}: {e}"
                        log.exception(msg)
                        result.errors.append(msg)
                    if progress:
                        progress("transfer", idx, len(mappings))
            except Exception as e:
                msg = f"Install step failed: {e}"
                log.exception(msg)
                result.errors.append(msg)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = "Install complete" if result.success else (
            "; ".join(result.errors) if result.errors else "Nothing transferred"
        )
        return result

    async def install_via_usb(
        self,
        item: InstallableItem,
        usb_root: str,
        progress: Optional[StageProgress] = None,
        usb_manager: Optional[UsbManager] = None,
        aurora_path: str = "Hdd:\\Aurora\\",
    ) -> InstallResult:
        usb_manager = usb_manager or UsbManager()
        result = InstallResult(success=False)
        files = _get_download_files(item)
        if not files:
            result.message = "No download files defined for this item"
            return result

        for df in files:
            tmp = Path(tempfile.mkdtemp(prefix="x360tm-"))
            try:
                root, local_files = await _prepare_local_files(df, self.downloader, tmp, progress)
                # Build same mapping as FTP, then translate Xbox path → USB local path
                resolved_paths = _resolve_aurora(df.install_paths, aurora_path)
                mappings = _resolve_remote_paths(resolved_paths, local_files, root)
                if progress:
                    progress("transfer", 0, len(mappings))
                for idx, (lf, remote) in enumerate(mappings, 1):
                    try:
                        dest = map_xbox_path_to_usb(remote, usb_root)
                        await asyncio.to_thread(usb_manager.copy_file, lf, dest)
                        result.files_transferred += 1
                    except Exception as e:
                        msg = f"Failed copying {lf.name} → {dest}: {e}"
                        log.exception(msg)
                        result.errors.append(msg)
                    if progress:
                        progress("transfer", idx, len(mappings))
            except Exception as e:
                msg = f"Install step failed: {e}"
                log.exception(msg)
                result.errors.append(msg)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = "Install complete" if result.success else (
            "; ".join(result.errors) if result.errors else "Nothing transferred"
        )
        return result

    async def download_only(
        self,
        item: InstallableItem,
        destination_dir: str | Path,
        progress: Optional[StageProgress] = None,
    ) -> list[Path]:
        files = _get_download_files(item)
        out: list[Path] = []
        dest = Path(destination_dir) / _get_item_name(item).replace("/", "_").replace("\\", "_")
        dest.mkdir(parents=True, exist_ok=True)

        def _cb(cur: int, total: int) -> None:
            if progress:
                progress("download", cur, total)

        for df in files:
            target = dest / (df.name or "download.bin")
            await self.downloader.download(df.url, target, progress_callback=_cb)
            out.append(target)
        return out

    # ------------------------------------------------------------------
    # GOD (Games on Demand) transfer
    # ------------------------------------------------------------------

    async def install_god_via_ftp(
        self,
        game: GodGameItem,
        ftp_client: FtpClient,
        dest_root: str,
        progress: Optional[StageProgress] = None,
    ) -> InstallResult:
        """Transfer all files of a GOD game to the console via FTP.

        Files are placed at:
          {dest_root}/{title_id}/{content_type}/{rel_path}
        """
        result = InstallResult(success=False)

        if not ftp_client.is_connected:
            try:
                await ftp_client.connect()
            except Exception as e:
                result.message = f"FTP connect failed: {e}"
                return result

        base = normalize_xbox_path(dest_root)
        if not base.endswith("/"):
            base += "/"
        base += f"{game.title_id}/{game.content_type}/"

        file_pairs = game.all_files()
        total_files = len(file_pairs)
        total_bytes = sum(lf.stat().st_size for lf, _ in file_pairs)
        bytes_done = 0

        if progress:
            progress("transfer", 0, total_bytes)

        for file_idx, (lf, rel) in enumerate(file_pairs, 1):
            remote = base + rel
            file_size = lf.stat().st_size
            base_bytes = bytes_done

            def _chunk_cb(sent: int, _unused_total: int, _base: int = base_bytes) -> None:
                if progress:
                    progress("transfer", _base + sent, total_bytes)

            try:
                await ftp_client.upload_file(lf, remote, progress_callback=_chunk_cb)
                result.files_transferred += 1
            except Exception as e:
                msg = f"Failed: {lf.name} → {remote}: {e}"
                log.exception(msg)
                result.errors.append(msg)
            bytes_done += file_size
            if progress:
                progress("transfer", bytes_done, total_bytes)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = (
            f"Transferred {result.files_transferred} of {total_files} file(s)" if result.success
            else "; ".join(result.errors) if result.errors
            else "Nothing transferred"
        )
        return result

    async def install_god_via_usb(
        self,
        game: GodGameItem,
        usb_root: str,
        dest_xbox_path: str,
        progress: Optional[StageProgress] = None,
        usb_manager: Optional[UsbManager] = None,
    ) -> InstallResult:
        """Copy all files of a GOD game to a USB drive.

        Files are placed at:
          {usb_root}/{dest_xbox_path}/{title_id}/{content_type}/{rel_path}
        """
        usb_mgr = usb_manager or UsbManager()
        result = InstallResult(success=False)

        base = normalize_xbox_path(dest_xbox_path)
        if not base.endswith("/"):
            base += "/"
        base += f"{game.title_id}/{game.content_type}/"

        file_pairs = game.all_files()
        total_files = len(file_pairs)
        total_bytes = sum(lf.stat().st_size for lf, _ in file_pairs)
        bytes_done = 0

        if progress:
            progress("transfer", 0, total_bytes)

        for file_idx, (lf, rel) in enumerate(file_pairs, 1):
            remote = base + rel
            file_size = lf.stat().st_size
            try:
                dest = map_xbox_path_to_usb(remote, usb_root)
                await asyncio.to_thread(usb_mgr.copy_file, lf, dest)
                result.files_transferred += 1
            except Exception as e:
                msg = f"Failed: {lf.name}: {e}"
                log.exception(msg)
                result.errors.append(msg)
            bytes_done += file_size
            if progress:
                progress("transfer", bytes_done, total_bytes)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = (
            f"Copied {result.files_transferred} of {total_files} file(s)" if result.success
            else "; ".join(result.errors) if result.errors
            else "Nothing transferred"
        )
        return result

    # ------------------------------------------------------------------
    # Title Update transfer
    # ------------------------------------------------------------------

    async def install_title_update_via_ftp(
        self,
        tu: TitleUpdateItem,
        ftp_client: FtpClient,
        game_drive: str = "Usb1",
        progress: Optional[StageProgress] = None,
    ) -> InstallResult:
        """Transfer a Title Update STFS package to the console via FTP.

        The TU is placed at the standard Xbox 360 content path:
          /{game_drive}/Content/0000000000000000/{TitleID}/000B0000/{filename}
        """
        result = InstallResult(success=False)

        if not ftp_client.is_connected:
            try:
                await ftp_client.connect()
            except Exception as e:
                result.message = f"FTP connect failed: {e}"
                return result

        remote_path = (
            f"/{game_drive}/Content/0000000000000000"
            f"/{tu.title_id}/000B0000/{tu.filename}"
        )
        total_bytes = tu.size_bytes

        if progress:
            progress("transfer", 0, total_bytes)

        def _chunk_cb(sent: int, _total: int) -> None:
            if progress:
                progress("transfer", sent, total_bytes)

        try:
            await ftp_client.upload_file(tu.local_path, remote_path, progress_callback=_chunk_cb)
            result.files_transferred = 1
        except Exception as e:
            msg = f"Failed to upload {tu.filename}: {e}"
            log.exception(msg)
            result.errors.append(msg)

        if progress:
            progress("transfer", total_bytes, total_bytes)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = (
            f"Title Update installed to {remote_path}" if result.success
            else "; ".join(result.errors)
        )
        return result

    async def install_title_update_via_usb(
        self,
        tu: TitleUpdateItem,
        usb_root: str,
        game_drive: str = "Usb1",
        progress: Optional[StageProgress] = None,
        usb_manager: Optional[UsbManager] = None,
    ) -> InstallResult:
        """Copy a Title Update STFS package to a USB drive.

        The TU is placed at:
          {usb_root}/Content/0000000000000000/{TitleID}/000B0000/{filename}
        """
        usb_mgr = usb_manager or UsbManager()
        result = InstallResult(success=False)

        # Build destination path on the USB mount
        dest_rel = f"Content/0000000000000000/{tu.title_id}/000B0000/{tu.filename}"
        dest = Path(usb_root) / dest_rel
        total_bytes = tu.size_bytes

        if progress:
            progress("transfer", 0, total_bytes)

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, tu.local_path, dest)
            result.files_transferred = 1
        except Exception as e:
            msg = f"Failed to copy {tu.filename}: {e}"
            log.exception(msg)
            result.errors.append(msg)

        if progress:
            progress("transfer", total_bytes, total_bytes)

        result.success = result.files_transferred > 0 and not result.errors
        result.message = (
            f"Title Update copied to {dest}" if result.success
            else "; ".join(result.errors)
        )
        return result
