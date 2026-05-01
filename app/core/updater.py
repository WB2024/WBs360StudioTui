"""Self-update via GitHub Releases API."""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

GITHUB_REPO = "WB2024/WBs360StudioTui"
_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
_SYSTEM = platform.system()  # "Linux" | "Windows" | "Darwin"

_LINUX_ASSET = "x360tm-linux.tar.gz"
_WINDOWS_ASSET = "x360tm-windows.zip"


def _asset_name() -> str:
    if _SYSTEM == "Linux":
        return _LINUX_ASSET
    elif _SYSTEM == "Windows":
        return _WINDOWS_ASSET
    else:
        raise NotImplementedError(f"Updates not supported on {_SYSTEM}")


def _parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' → (1, 2, 3)"""
    parts = re.findall(r"\d+", tag.lstrip("v"))
    return tuple(int(p) for p in parts) if parts else (0,)


@dataclass
class UpdateInfo:
    tag: str
    version_tuple: tuple[int, ...]
    download_url: str
    asset_name: str
    body: str
    is_prerelease: bool


async def check_for_update(channel: str, current_version: str) -> UpdateInfo | None:
    """
    Return UpdateInfo if a newer release exists on GitHub, else None.
    channel: 'latest' | 'pre-release'
    """
    try:
        asset = _asset_name()
    except NotImplementedError:
        return None

    current = _parse_version(current_version)
    include_pre = channel == "pre-release"

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(
            f"{_API_BASE}?per_page=20",
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        releases = resp.json()

    for rel in releases:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not include_pre:
            continue
        tag = rel.get("tag_name", "")
        ver = _parse_version(tag)
        if ver <= current:
            continue
        for a in rel.get("assets", []):
            if a["name"] == asset:
                return UpdateInfo(
                    tag=tag,
                    version_tuple=ver,
                    download_url=a["browser_download_url"],
                    asset_name=a["name"],
                    body=rel.get("body", ""),
                    is_prerelease=rel.get("prerelease", False),
                )
    return None


async def download_update(
    info: UpdateInfo,
    dest_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the release asset to dest_dir. Returns path to downloaded file."""
    dest = dest_dir / info.asset_name
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", info.download_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            received = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, total)
    return dest


def _current_exe() -> Path:
    """Path to the running executable (frozen) or script (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.argv[0])


def apply_update(archive: Path) -> bool:
    """
    Apply the downloaded update archive.

    Returns True  → update applied in-place; caller should call restart_app()
                    after the Textual app exits (Linux).
    Returns False → a helper process has been launched that will replace the
                    binary and restart; caller should exit the app (Windows).
    Raises on failure.
    """
    if _SYSTEM == "Linux":
        _apply_linux(archive)
        return True
    elif _SYSTEM == "Windows":
        _apply_windows(archive)
        return False
    else:
        raise NotImplementedError(f"Updates not supported on {_SYSTEM}")


def _apply_linux(archive: Path) -> None:
    """Extract binary from tar.gz and atomically replace the running exe."""
    exe = _current_exe()
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(Path(tmp))
        new_bin = Path(tmp) / "x360tm"
        if not new_bin.exists():
            raise FileNotFoundError("x360tm binary not found in archive")
        new_bin.chmod(0o755)
        # Stage in the same directory as the exe so the rename is on the
        # same filesystem — os.replace (rename) fails across devices (EXDEV)
        # which is common when /tmp is tmpfs and the binary lives on ext4.
        stage = exe.with_name(f".x360tm_update_{os.getpid()}")
        try:
            shutil.copy2(new_bin, stage)
            stage.chmod(0o755)
            os.replace(stage, exe)
        except Exception:
            stage.unlink(missing_ok=True)
            raise


def _apply_windows(archive: Path) -> None:
    """
    Extract new exe to a temp dir, write a PowerShell helper that copies it
    over the running exe after this process exits, then relaunches it.
    """
    exe = _current_exe()
    tmp = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(tmp)
    new_exe = tmp / "x360tm.exe"
    if not new_exe.exists():
        raise FileNotFoundError("x360tm.exe not found in archive")

    # Escape single quotes in paths for use inside PS single-quoted strings
    old_path = str(exe).replace("'", "''")
    new_path = str(new_exe).replace("'", "''")

    helper = tmp / "_x360tm_updater.ps1"
    helper.write_text(
        f"Start-Sleep -Seconds 2\n"
        f"Copy-Item -Force '{new_path}' '{old_path}'\n"
        f"Start-Process '{old_path}'\n"
        f"Remove-Item -Force $PSCommandPath -ErrorAction SilentlyContinue\n",
        encoding="utf-8",
    )

    import subprocess
    subprocess.Popen(
        [
            "powershell.exe",
            "-WindowStyle", "Hidden",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(helper),
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def restart_app() -> None:
    """Re-exec the current process (call on Linux after apply_update returns True)."""
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
