"""USB block-level backup and restore using partclone + zstd.

Linux only. All block-level operations (partclone, parted, fatresize) require
sudo and are run as async subprocesses so the Textual event loop stays alive.

Public API
----------
check_platform()            -> bool
check_dependencies()        -> dict[str, bool]
detect_removable_devices()  -> list[BlockDevice]
query_used_bytes(partition) -> int
check_restore_compat(meta, target_bytes) -> RestoreMode
list_backups(backup_dir)    -> list[BackupMeta]
create_backup(...)          -> BackupMeta
restore_backup(...)         -> None
get_backup_dir(settings)    -> Path
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

import app as app_mod

log = logging.getLogger(__name__)

# Volume label BadBuilder always uses for the exploit USB
BADUPDATE_LABEL = "BADUPDATE"

# Safety: never operate on devices with any partition mounted here
_OS_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var", "/run/systemd"}

# Required external tools
REQUIRED_TOOLS = ["partclone.fat", "zstd", "parted", "fatresize"]

# Extra directories to search when shutil.which misses tools in /usr/sbin
_SBIN_DIRS = ["/usr/sbin", "/sbin", "/usr/local/sbin"]


def _find_tool(name: str) -> str | None:
    """Like shutil.which but also checks sbin directories not always in PATH."""
    found = shutil.which(name)
    if found:
        return found
    for d in _SBIN_DIRS:
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _tool_path(name: str) -> str:
    """Return full path for a required tool, raise RuntimeError if absent."""
    p = _find_tool(name)
    if not p:
        raise RuntimeError(
            f"Required tool not found: {name}. "
            "Install it with: sudo apt install partclone zstd parted fatresize"
        )
    return p


async def sudo_authenticate(password: str) -> bool:
    """
    Pre-authenticate sudo by running 'sudo -S true' with the given password.

    Returns True on success, False if the password was wrong.
    Caches sudo credentials for the session so subsequent sudo calls
    proceed without further prompting.
    """
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-S", "-p", "", "true",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    pw_bytes = (password + "\n").encode()
    try:
        await asyncio.wait_for(proc.communicate(input=pw_bytes), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        return False
    return proc.returncode == 0

# Overhead multiplier: target must be >= used_bytes * this factor
_OVERHEAD_FACTOR = 1.05

# Repo root: 3 levels up from app/core/usb_backup.py
_REPO_ROOT = Path(__file__).parent.parent.parent

_PARTCLONE_PROGRESS_RE = re.compile(
    r"Elapsed:\s*[\d:]+,\s*Remaining:\s*[\d:]+,\s*Completed:\s*([\d.]+)%"
)
_PARTCLONE_USED_RE = re.compile(r"used blocks\s*:\s*(\d+)\s*(?:of\s*\d+)?\s*blocks?", re.IGNORECASE)
_PARTCLONE_BLOCK_SIZE_RE = re.compile(r"block size\s*:\s*(\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class BlockDevice:
    device: str           # /dev/sdb
    partition: str        # /dev/sdb1
    label: str            # BADUPDATE
    filesystem: str       # vfat
    total_bytes: int
    partition_bytes: int
    mountpoint: str | None
    is_suggested: bool    # True when label == BADUPDATE_LABEL


class RestoreMode(str, Enum):
    EXACT = "exact"           # target >= source partition
    SHRINK = "shrink"         # target < source but >= used data
    TOO_SMALL = "too_small"   # target < used data — hard fail


@dataclass
class BackupMeta:
    x360tm_version: str
    timestamp_utc: str
    volume_label: str
    source_device: str
    source_partition: str
    filesystem: str
    source_disk_total_bytes: int
    partition_start_bytes: int
    partition_size_bytes: int
    used_bytes: int
    partclone_version: str
    zstd_level: int
    image_file: str         # filename only (no dir component)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BackupMeta":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})

    @property
    def timestamp_display(self) -> str:
        try:
            dt = datetime.fromisoformat(self.timestamp_utc.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d  %H:%M")
        except Exception:
            return self.timestamp_utc

    @property
    def used_gib(self) -> float:
        return self.used_bytes / (1024 ** 3)

    @property
    def total_gib(self) -> float:
        return self.source_disk_total_bytes / (1024 ** 3)


# ---------------------------------------------------------------------------
# Platform + dependency checks
# ---------------------------------------------------------------------------

def check_platform() -> bool:
    """Return True only when running on Linux."""
    return sys.platform.startswith("linux")


def check_dependencies() -> dict[str, bool]:
    """Return a mapping of tool → available for all required tools."""
    return {tool: _find_tool(tool) is not None for tool in REQUIRED_TOOLS}


def all_dependencies_present() -> bool:
    return all(check_dependencies().values())


# ---------------------------------------------------------------------------
# Backup directory resolution
# ---------------------------------------------------------------------------

def get_backup_dir(settings) -> Path:
    """Resolve the backup directory from settings or fall back to repo root."""
    raw = getattr(settings, "backup_dir", "") or ""
    if raw.strip():
        return Path(raw.strip())
    # Frozen build: fall back to home
    if getattr(sys, "frozen", False):
        return Path.home() / "x360tm-backups"
    return _REPO_ROOT / "USBBackups"


# ---------------------------------------------------------------------------
# Device detection
# ---------------------------------------------------------------------------

def _is_removable(rm_val) -> bool:
    """Handle rm field from lsblk JSON which may be bool, int, or string."""
    if isinstance(rm_val, bool):
        return rm_val
    return str(rm_val) in ("1", "true")


def detect_removable_devices() -> list[BlockDevice]:
    """Run lsblk -J and return safe removable devices with FAT filesystems.

    Handles two layouts:
      - Partitioned disk: sdc → sdc1 (children of type 'part')
      - Partitionless disk: FAT32 written directly to sdc with no partition table
        (common for BadBuilder USB sticks; lsblk shows the disk itself with a
        fstype but no children)
    """
    import subprocess
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,RM,FSTYPE,LABEL,MOUNTPOINT,TYPE,PKNAME"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("lsblk failed: %s", e)
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        log.error("lsblk JSON parse failed")
        return []

    devices: list[BlockDevice] = []
    block_devices = data.get("blockdevices", [])

    for disk in block_devices:
        if disk.get("type") != "disk":
            continue
        if not _is_removable(disk.get("rm", 0)):
            continue

        # Skip empty devices (card slots with no media)
        try:
            disk_bytes = int(disk.get("size") or 0)
        except (ValueError, TypeError):
            disk_bytes = 0
        if disk_bytes == 0:
            continue

        device_path = f"/dev/{disk['name']}"
        children = disk.get("children") or []

        # --- Partitionless disk (FAT written directly to the raw device) ---
        if not children:
            fs = (disk.get("fstype") or "").lower()
            if not fs:
                continue  # unformatted / unreadable
            mp = disk.get("mountpoint") or None
            if mp and _is_os_mountpoint(mp):
                continue
            label = disk.get("label") or ""
            devices.append(BlockDevice(
                device=device_path,
                partition=device_path,  # device == partition for partitionless
                label=label,
                filesystem=fs,
                total_bytes=disk_bytes,
                partition_bytes=disk_bytes,
                mountpoint=mp,
                is_suggested=(label.upper() == BADUPDATE_LABEL),
            ))
            continue

        # --- Partitioned disk: inspect each partition ---
        for part in children:
            if part.get("type") != "part":
                continue

            mp = part.get("mountpoint") or None
            fs = (part.get("fstype") or "").lower()
            label = part.get("label") or ""

            if mp and _is_os_mountpoint(mp):
                continue

            try:
                part_bytes = int(part.get("size") or 0)
            except (ValueError, TypeError):
                part_bytes = 0

            devices.append(BlockDevice(
                device=device_path,
                partition=f"/dev/{part['name']}",
                label=label,
                filesystem=fs,
                total_bytes=disk_bytes,
                partition_bytes=part_bytes,
                mountpoint=mp,
                is_suggested=(label.upper() == BADUPDATE_LABEL),
            ))

    return devices


def _is_os_mountpoint(mp: str) -> bool:
    if mp in _OS_MOUNTPOINTS:
        return True
    if mp.startswith("/snap/"):
        return True
    return False


# ---------------------------------------------------------------------------
# partclone version
# ---------------------------------------------------------------------------

async def _get_partclone_version() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            _tool_path("partclone.fat"), "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        # partclone writes version to stdout
        text = stdout.decode("utf-8", errors="replace") or stderr.decode("utf-8", errors="replace")
        m = re.search(r"v?(\d+\.\d+[.\d]*)", text)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Query used bytes from a partition
# ---------------------------------------------------------------------------

async def query_used_bytes(partition: str) -> int:
    """Return used bytes on the partition. Uses df (fast, no sudo) if mounted, else partclone --info."""
    # Try df first — works on a mounted filesystem, no sudo needed
    try:
        proc = await asyncio.create_subprocess_exec(
            "df", "-B1", "--output=used", partition,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        # Output is header + value, e.g.: "     Used\n1234567890"
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.isdigit():
                return int(stripped)
    except Exception as e:
        log.warning("df query_used_bytes failed (%s), falling back to partclone --info", e)

    # Fallback: partclone --info reads raw device (works unmounted, needs sudo)
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", _tool_path("partclone.fat"), "--info", "--source", partition,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    # partclone --info writes to stderr
    text = stderr.decode("utf-8", errors="replace") + stdout.decode("utf-8", errors="replace")

    used_blocks = 0
    block_size = 512

    for line in text.splitlines():
        mu = _PARTCLONE_USED_RE.search(line)
        if mu:
            used_blocks = int(mu.group(1))
        mb = _PARTCLONE_BLOCK_SIZE_RE.search(line)
        if mb:
            block_size = int(mb.group(1))

    return used_blocks * block_size


# ---------------------------------------------------------------------------
# Restore compatibility check
# ---------------------------------------------------------------------------

def check_restore_compat(meta: BackupMeta, target_partition_bytes: int) -> RestoreMode:
    """Determine whether restore is possible and which path to take."""
    if target_partition_bytes >= meta.partition_size_bytes:
        return RestoreMode.EXACT
    min_required = int(meta.used_bytes * _OVERHEAD_FACTOR)
    if target_partition_bytes >= min_required:
        return RestoreMode.SHRINK
    return RestoreMode.TOO_SMALL


# ---------------------------------------------------------------------------
# Backup list
# ---------------------------------------------------------------------------

def list_backups(backup_dir: Path) -> list[BackupMeta]:
    """Scan backup_dir for *.meta.json files and return BackupMeta list, newest first."""
    if not backup_dir.exists():
        return []
    metas: list[BackupMeta] = []
    for meta_path in sorted(backup_dir.glob("*.meta.json"), reverse=True):
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                d = json.load(f)
            metas.append(BackupMeta.from_dict(d))
        except Exception as e:
            log.warning("Could not read backup meta %s: %s", meta_path.name, e)
    return metas


def backup_image_path(backup_dir: Path, meta: BackupMeta) -> Path:
    return backup_dir / meta.image_file


# ---------------------------------------------------------------------------
# Mount / unmount helpers
# ---------------------------------------------------------------------------

async def _unmount_device(partition: str) -> str | None:
    """
    Unmount a partition if it is currently mounted.
    Returns the mountpoint string if unmounted, None if it wasn't mounted.
    Raises RuntimeError if unmount fails.
    """
    import subprocess
    result = subprocess.run(
        ["findmnt", "-n", "-o", "TARGET", partition],
        capture_output=True, text=True,
    )
    mountpoint = result.stdout.strip()
    if not mountpoint:
        return None  # not mounted
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "umount", partition,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to unmount {partition}: "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )
    return mountpoint


async def _remount_device(partition: str) -> None:
    """
    Attempt to remount a partition using udisksctl (no sudo needed via polkit).
    Non-fatal — just logs a warning on failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "udisksctl", "mount", "-b", partition,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
        log.info("Remounted %s via udisksctl", partition)
    except Exception as e:
        log.warning("Could not remount %s (non-fatal): %s", partition, e)




async def create_backup(
    device: BlockDevice,
    backup_dir: Path,
    progress_cb: Callable[[float, str], None],
) -> BackupMeta:
    """
    Back up device.partition to backup_dir using partclone.fat | zstd.

    progress_cb(pct: float 0–100, status: str) is called as partclone progresses.
    Raises RuntimeError on failure. Only writes meta.json on full success.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    safe_label = re.sub(r"[^A-Za-z0-9_-]", "_", device.label or "USB")
    ts_file = now.strftime("%Y-%m-%d_%H%M%S")
    ts_utc = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    base_name = f"usb_backup_{safe_label}_{ts_file}"
    image_path = backup_dir / f"{base_name}.pcl.zst"
    meta_path = backup_dir / f"{base_name}.meta.json"

    partclone_version = await _get_partclone_version()

    # Step 1: query used bytes while device is still mounted (df is most reliable)
    progress_cb(0.0, "Querying partition usage…")
    try:
        used_bytes = await query_used_bytes(device.partition)
    except Exception as e:
        log.warning("query_used_bytes failed (%s), continuing with 0", e)
        used_bytes = 0

    # Step 2: unmount if mounted (partclone refuses to clone mounted filesystems)
    progress_cb(1.0, "Checking mount status…")
    mountpoint = await _unmount_device(device.partition)
    if mountpoint:
        progress_cb(1.5, f"Unmounted {device.partition} from {mountpoint}")

    # Step 3: get partition start offset via parted
    partition_start = await _get_partition_start_bytes(device.device, device.partition)

    progress_cb(2.0, f"Starting backup of {device.partition}…")

    # Step 3: run partclone.fat --clone | zstd
    # Use an OS-level pipe so both subprocesses share a real fd (StreamReader
    # has no fileno() so it cannot be passed as stdin to a second process).
    partclone_cmd = [
        "sudo", "-n", _tool_path("partclone.fat"),
        "--clone",
        "--source", device.partition,
        "--output", "-",
    ]
    zstd_cmd = [_tool_path("zstd"), "-T0", "-3", "-o", str(image_path), "--force"]

    pipe_r, pipe_w = os.pipe()
    try:
        p1 = await asyncio.create_subprocess_exec(
            *partclone_cmd,
            stdout=pipe_w,
            stderr=asyncio.subprocess.PIPE,
        )
        os.close(pipe_w)  # parent doesn't write; closing lets p2 see EOF when p1 exits
        pipe_w = -1

        p2 = await asyncio.create_subprocess_exec(
            *zstd_cmd,
            stdin=pipe_r,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        os.close(pipe_r)
        pipe_r = -1
    except Exception:
        for fd in (pipe_r, pipe_w):
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise

    # Stream partclone stderr for progress
    partclone_stderr_lines: list[str] = []
    async def _read_partclone_stderr():
        assert p1.stderr is not None
        async for raw in p1.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                partclone_stderr_lines.append(line)
                m = _PARTCLONE_PROGRESS_RE.search(line)
                if m:
                    pct = float(m.group(1))
                    mapped = 2.0 + pct * 0.93
                    progress_cb(mapped, f"Backing up… {pct:.1f}%")

    await asyncio.gather(
        _read_partclone_stderr(),
        p1.wait(),
    )
    await p2.wait()

    if p1.returncode != 0:
        stderr_tail = "\n".join(partclone_stderr_lines[-10:])
        # Clean up partial image
        if image_path.exists():
            image_path.unlink(missing_ok=True)
        if mountpoint:
            await _remount_device(device.partition)
        raise RuntimeError(
            f"partclone.fat exited with code {p1.returncode}.\n{stderr_tail}"
        )
    if p2.returncode != 0:
        zstd_err = b""
        if p2.stderr:
            zstd_err = await p2.stderr.read()
        if image_path.exists():
            image_path.unlink(missing_ok=True)
        if mountpoint:
            await _remount_device(device.partition)
        raise RuntimeError(
            f"zstd exited with code {p2.returncode}.\n{zstd_err.decode('utf-8', errors='replace')}"
        )

    if not image_path.exists() or image_path.stat().st_size == 0:
        if mountpoint:
            await _remount_device(device.partition)
        raise RuntimeError("Backup image is missing or empty after write.")

    progress_cb(97.0, "Writing metadata…")

    meta = BackupMeta(
        x360tm_version=app_mod.__version__,
        timestamp_utc=ts_utc,
        volume_label=device.label,
        source_device=device.device,
        source_partition=device.partition,
        filesystem=device.filesystem,
        source_disk_total_bytes=device.total_bytes,
        partition_start_bytes=partition_start,
        partition_size_bytes=device.partition_bytes,
        used_bytes=used_bytes,
        partclone_version=partclone_version,
        zstd_level=3,
        image_file=image_path.name,
    )

    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta.to_dict(), f, indent=2)

    progress_cb(99.0, "Remounting USB…")
    if mountpoint:
        await _remount_device(device.partition)

    progress_cb(100.0, "Backup complete.")
    return meta


async def _get_partition_start_bytes(device: str, partition: str) -> int:
    """Use parted to get the partition start offset in bytes."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", _tool_path("parted"), "-s", "-m", device, "unit", "B", "print",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        text = stdout.decode("utf-8", errors="replace")
        part_num = re.search(r"/dev/\w+(\d+)$", partition)
        if not part_num:
            return 1048576  # 1 MiB default
        num = part_num.group(1)
        for line in text.splitlines():
            fields = line.rstrip(";").split(":")
            if fields and fields[0] == num:
                # Format: num:start:end:size:fs:name:flags;
                start_str = fields[1].rstrip("B")
                return int(start_str)
    except Exception as e:
        log.warning("_get_partition_start_bytes failed: %s", e)
    return 1048576


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

async def restore_backup(
    meta: BackupMeta,
    image_path: Path,
    target_device: BlockDevice,
    progress_cb: Callable[[float, str], None],
) -> None:
    """
    Restore a partclone backup image to target_device.

    Handles three cases:
      EXACT  — partclone restore directly, then expand FS if target is larger
      SHRINK — repartition target first, then restore, then resize FS
      TOO_SMALL — raises before doing anything

    Raises RuntimeError on failure.
    """
    mode = check_restore_compat(meta, target_device.partition_bytes)

    if mode == RestoreMode.TOO_SMALL:
        used_gib = meta.used_bytes / (1024 ** 3)
        target_gib = target_device.partition_bytes / (1024 ** 3)
        raise RuntimeError(
            f"Target device is too small.\n"
            f"Backup used data: {used_gib:.2f} GiB\n"
            f"Target partition: {target_gib:.2f} GiB\n"
            f"At least {used_gib * _OVERHEAD_FACTOR:.2f} GiB required."
        )

    partition = target_device.partition
    device = target_device.device

    # Unmount the target if it is mounted — partclone refuses to write to mounted fs
    progress_cb(1.0, "Checking mount status…")
    mountpoint = await _unmount_device(partition)
    if mountpoint:
        progress_cb(2.0, f"Unmounted {partition} from {mountpoint}")

    if mode == RestoreMode.SHRINK:
        # Repartition the target device to a fresh MBR + single FAT32 partition
        progress_cb(2.0, f"Repartitioning {device}…")
        parted = _tool_path("parted")
        await _run_sudo_cmd(
            [parted, "-s", device, "mklabel", "msdos"],
            "parted mklabel",
        )
        await _run_sudo_cmd(
            [parted, "-s", device, "mkpart", "primary", "fat32", "1MiB", "100%"],
            "parted mkpart",
        )
        await _run_sudo_cmd(
            [parted, "-s", device, "set", "1", "boot", "on"],
            "parted set boot",
        )
        # Flush kernel partition table so /dev/sdc1 node is ready before partclone
        try:
            await _run_sudo_cmd(["partprobe", device], "partprobe")
        except Exception:
            pass  # non-fatal, udevadm settle below is the safety net
        proc_settle = await asyncio.create_subprocess_exec(
            "udevadm", "settle", "--timeout=10",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc_settle.wait(), timeout=15)
        progress_cb(8.0, "Repartition complete.")

    # Restore the image
    progress_cb(10.0, f"Restoring to {partition}…")

    # Use an OS-level pipe: zstd stdout → partclone stdin
    pipe_r, pipe_w = os.pipe()
    try:
        zstd_proc = await asyncio.create_subprocess_exec(
            _tool_path("zstd"), "-d", "-T0", str(image_path), "--stdout",
            stdout=pipe_w,
            stderr=asyncio.subprocess.DEVNULL,
        )
        os.close(pipe_w)
        pipe_w = -1

        partclone_proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", _tool_path("partclone.fat"),
            "--restore",
            "--output", partition,
            stdin=pipe_r,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        os.close(pipe_r)
        pipe_r = -1
    except Exception:
        for fd in (pipe_r, pipe_w):
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise

    partclone_err_lines: list[str] = []
    async def _read_restore_stderr():
        assert partclone_proc.stderr is not None
        async for raw in partclone_proc.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                partclone_err_lines.append(line)
                m = _PARTCLONE_PROGRESS_RE.search(line)
                if m:
                    pct = float(m.group(1))
                    mapped = 10.0 + pct * 0.80
                    progress_cb(mapped, f"Restoring… {pct:.1f}%")

    await asyncio.gather(
        _read_restore_stderr(),
        zstd_proc.wait(),
        partclone_proc.wait(),
    )

    # Check partclone first — if it fails, zstd gets SIGPIPE (-13) which is misleading
    if partclone_proc.returncode != 0:
        tail = "\n".join(partclone_err_lines[-10:])
        raise RuntimeError(
            f"partclone.fat restore failed (exit {partclone_proc.returncode}).\n{tail}"
        )
    if zstd_proc.returncode != 0:
        raise RuntimeError(f"zstd decompression failed (exit {zstd_proc.returncode}).")

    # Resize FAT32 filesystem to fill the (possibly new/different) partition
    progress_cb(92.0, "Resizing filesystem…")

    if mode == RestoreMode.EXACT and target_device.partition_bytes > meta.partition_size_bytes:
        # Target is larger: expand partition first
        try:
            await _run_sudo_cmd(
                ["parted", "-s", device, "resizepart", "1", "100%"],
                "parted resizepart",
            )
        except RuntimeError as e:
            log.warning("parted resizepart failed (non-fatal): %s", e)

    try:
        await _run_sudo_cmd(
            ["fatresize", "-s", "max", partition],
            "fatresize",
        )
        progress_cb(98.0, "Filesystem resized.")
    except RuntimeError as e:
        # fatresize failure is non-fatal — warn but don't abort
        log.warning("fatresize failed (non-fatal): %s", e)
        progress_cb(98.0, f"[yellow]Warning: fatresize failed — {e}[/]")

    progress_cb(100.0, "Restore complete.")


async def _run_sudo_cmd(cmd: list[str], label: str) -> str:
    """Run a sudo -n command, return stdout, raise RuntimeError on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{label} failed (exit {proc.returncode}): {err}")
    return stdout.decode("utf-8", errors="replace")
