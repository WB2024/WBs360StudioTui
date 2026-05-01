"""Create BadAvatar USB — core operations.

Linux only. Formats a USB drive as partitionless FAT32 (BADUPDATE label),
mirrors the ABadAvatar v1.1 + XeUnshackle 1.03 source files, renames the
Aurora folder, and optionally patches launch.ini to auto-boot Aurora.

No internet connection required — all source files live in BadAvatarFiles/
at the repo root (gitignored; not committed due to size and copyright).

See BADAVATAR_USB_SPEC.md §4 for the full operations sequence.

Public API
----------
check_platform()                            -> bool
check_source_files()                        -> tuple[bool, str]
get_source_dir()                            -> Path
format_and_mount(device, pw, cb)            -> Path
copy_files(source, dest, cb)               -> None   (async)
rename_aurora(dest_mount)                   -> None
patch_launch_ini(dest_mount, set_default)   -> None
write_info_txt(dest_mount)                  -> None
sync_and_unmount(device, pw, cb)            -> None   (async)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo root: three levels up from app/core/bad_avatar.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Source files directory (gitignored — populated by developer from WBs360 workspace)
BAD_AVATAR_SOURCE_DIR = _REPO_ROOT / "BadAvatarFiles"

# Volume label applied during format
BADAVATAR_USB_LABEL = "BADUPDATE"

# Aurora folder name in the source package
_AURORA_SRC_NAME = "Aurora 0.7b.2"

# Desired folder name on the USB
_AURORA_DEST_NAME = "Aurora"

# Aurora executable path as seen by the Xbox 360 (used in launch.ini Default=)
AURORA_LAUNCH_PATH = r"Usb:\Apps\Aurora\Aurora.xex"

# Base directory for USB mountpoints
_MOUNT_BASE = Path("/media") / os.environ.get("USER", "user")


# ---------------------------------------------------------------------------
# Platform + source checks
# ---------------------------------------------------------------------------

def check_platform() -> bool:
    """Return True only when running on Linux."""
    return sys.platform.startswith("linux")


def check_source_files() -> tuple[bool, str]:
    """Return (ok, message).

    ok is True when BadAvatarFiles/ exists and contains at least the
    BadUpdatePayload/ directory (the minimum indicator of a valid install).
    """
    if not BAD_AVATAR_SOURCE_DIR.exists():
        return False, (
            f"BadAvatar source files not found.\n\n"
            f"Expected: {BAD_AVATAR_SOURCE_DIR}\n\n"
            f"Populate it by copying from:\n"
            f"  WBs360/Tools/Extracted/ABadAvatar v1.1 + XeUnshackle 1.03 Exploit Xbox 360/"
        )
    payload = BAD_AVATAR_SOURCE_DIR / "BadUpdatePayload"
    if not payload.is_dir():
        return False, (
            f"BadAvatar source files appear incomplete.\n\n"
            f"Expected: {BAD_AVATAR_SOURCE_DIR}/BadUpdatePayload/\n\n"
            f"Re-copy from:\n"
            f"  WBs360/Tools/Extracted/ABadAvatar v1.1 + XeUnshackle 1.03 Exploit Xbox 360/"
        )
    return True, "OK"


def get_source_dir() -> Path:
    return BAD_AVATAR_SOURCE_DIR


# ---------------------------------------------------------------------------
# Privileged subprocess helper
# ---------------------------------------------------------------------------

async def _run_sudo(
    *cmd: str,
    sudo_password: str,
    check: bool = True,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a command under sudo -S, feeding the password via stdin.

    Returns (returncode, stdout, stderr).
    Raises RuntimeError when check=True and returncode != 0.
    """
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-S", "-p", "", *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    pw_bytes = (sudo_password + "\n").encode()
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=pw_bytes), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out after {timeout}s: sudo {' '.join(cmd)}")

    rc = proc.returncode
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if check and rc != 0:
        raise RuntimeError(
            f"Command failed (rc={rc}): sudo {' '.join(cmd)}\nstderr: {stderr.strip()}"
        )
    return rc, stdout, stderr


# ---------------------------------------------------------------------------
# Format + mount
# ---------------------------------------------------------------------------

async def format_and_mount(
    device: str,
    sudo_password: str,
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    """Unmount the device, format as partitionless FAT32, mount, return mount path.

    Uses mkfs.vfat -F 32 directly on the block device (no partition table),
    matching the format BadBuilder produces. udisks2 will NOT auto-mount this
    layout — we mount explicitly.
    """
    def _log(msg: str) -> None:
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    mount_path = _MOUNT_BASE / BADAVATAR_USB_LABEL

    _log(f"Unmounting {device} (if mounted)...")
    await _run_sudo("umount", device, sudo_password=sudo_password, check=False)

    _log(f"Formatting {device} as FAT32 with label {BADAVATAR_USB_LABEL}...")
    await _run_sudo(
        "mkfs.vfat", "-F", "32", "-n", BADAVATAR_USB_LABEL, device,
        sudo_password=sudo_password,
    )

    _log(f"Creating mountpoint: {mount_path}")
    await _run_sudo("mkdir", "-p", str(mount_path), sudo_password=sudo_password)

    _log(f"Mounting {device} at {mount_path}...")
    await _run_sudo(
        "mount",
        "-o", f"uid={os.getuid()},gid={os.getgid()}",
        device, str(mount_path),
        sudo_password=sudo_password,
    )

    _log(f"Mounted at {mount_path}")
    return mount_path


# ---------------------------------------------------------------------------
# File copy
# ---------------------------------------------------------------------------

async def copy_files(
    source: Path,
    dest: Path,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    """Mirror source tree to dest, reporting progress per file.

    Runs in a thread pool so the Textual event loop stays alive during the
    ~251 MB copy. progress_cb receives (relative_path, files_done, total_files)
    and is called from the thread — callers must use call_from_thread when
    updating TUI widgets.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _copy_files_sync, source, dest, progress_cb)


def _copy_files_sync(
    source: Path,
    dest: Path,
    progress_cb: Callable[[str, int, int], None] | None,
) -> None:
    import shutil

    all_files = [p for p in source.rglob("*") if p.is_file()]
    total = len(all_files)

    for i, src_file in enumerate(all_files):
        rel = src_file.relative_to(source)
        dst_file = dest / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        if progress_cb:
            progress_cb(str(rel), i + 1, total)


# ---------------------------------------------------------------------------
# Aurora folder rename
# ---------------------------------------------------------------------------

def rename_aurora(dest_mount: Path) -> None:
    """Rename Apps/Aurora 0.7b.2/ → Apps/Aurora/ on the USB.

    The source package ships with "Aurora 0.7b.2" as the folder name.
    The Xbox 360 DashLaunch launch.ini expects the path Usb:\\Apps\\Aurora\\Aurora.xex,
    so we rename the folder to match.
    """
    apps_dir = dest_mount / "Apps"
    src = apps_dir / _AURORA_SRC_NAME
    dst = apps_dir / _AURORA_DEST_NAME

    if dst.exists():
        log.info("Aurora destination already exists: %s — skipping rename", dst)
        return

    if not src.exists():
        log.warning("Aurora source not found: %s — skipping rename", src)
        return

    src.rename(dst)
    log.info("Renamed: %s → %s", _AURORA_SRC_NAME, _AURORA_DEST_NAME)


# ---------------------------------------------------------------------------
# launch.ini patch
# ---------------------------------------------------------------------------

def patch_launch_ini(dest_mount: Path, set_aurora_default: bool) -> None:
    """Patch (or skip) the Default= line in launch.ini on the USB.

    When set_aurora_default is True, replaces the blank Default= value with
    AURORA_LAUNCH_PATH so Aurora boots automatically on power-on.

    When False, the file is left as-is (Default= remains blank from the
    source package — user must configure DashLaunch manually on the console).
    """
    ini_path = dest_mount / "launch.ini"

    if not ini_path.exists():
        log.warning("launch.ini not found at %s — skipping patch", ini_path)
        return

    if not set_aurora_default:
        log.info("Aurora default not requested — launch.ini left unchanged")
        return

    # Read and normalise to LF so \s* in the regex doesn't consume \r from
    # CRLF line endings (the launch.ini ships with Windows line endings).
    text = ini_path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")

    # Match "Default = " on its own line. Use [ \t]* (not \s*) to avoid
    # the greedy \s* consuming newlines across blank lines below the key.
    # Use a lambda replacement to avoid re.sub interpreting backslashes in
    # the Windows-style path (e.g. \A in Usb:\Apps looks like \A escape).
    new_text = re.sub(
        r"(?m)^Default[ \t]*=[ \t]*.*$",
        lambda m: "Default = " + AURORA_LAUNCH_PATH,
        text,
    )

    if new_text == text:
        log.warning("launch.ini: Default= line not found or pattern unmatched — not patched")
        return

    ini_path.write_text(new_text, encoding="utf-8")
    log.info("launch.ini patched: Default = %s", AURORA_LAUNCH_PATH)


# ---------------------------------------------------------------------------
# info.txt
# ---------------------------------------------------------------------------

def write_info_txt(dest_mount: Path) -> None:
    """Write info.txt to the USB root."""
    try:
        import app as app_mod
        version = getattr(app_mod, "__version__", "unknown")
    except Exception:
        version = "unknown"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        "This drive was created with x360tm by WillBurns.\n"
        "Find more info here: https://github.com/WillBurns/WBs360StudioTui\n"
        "\n"
        "Exploit:     ABadAvatar v1.1 + XeUnshackle 1.03\n"
        "Payload:     XeUnshackle 1.03 (default.xex)\n"
        "Dashboard:   Aurora 0.7b.2\n"
        "Source:      Workspace (no download)\n"
        f"Created:     {now}\n"
        f"Tool:        x360tm v{version}\n"
        "Platform:    Linux\n"
    )

    info_path = dest_mount / "info.txt"
    info_path.write_text(content, encoding="utf-8")
    log.info("info.txt written to %s", info_path)


# ---------------------------------------------------------------------------
# Sync + unmount
# ---------------------------------------------------------------------------

async def sync_and_unmount(
    device: str,
    sudo_password: str,
    progress_cb: Callable[[str], None] | None = None,
) -> None:
    """Flush filesystem buffers then unmount the device."""
    def _log(msg: str) -> None:
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log("Syncing filesystem buffers...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sync",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=60)
    except Exception as e:
        log.warning("sync failed: %s", e)

    _log(f"Unmounting {device}...")
    rc, _, stderr = await _run_sudo(
        "umount", device, sudo_password=sudo_password, check=False
    )
    if rc != 0:
        log.warning("umount returned %d: %s", rc, stderr.strip())
        _log(f"[yellow]Warning: unmount returned rc={rc} — eject the drive safely before removing it.[/]")
    else:
        _log("Unmounted successfully.")
