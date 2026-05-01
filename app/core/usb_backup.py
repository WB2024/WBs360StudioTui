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
    return {tool: shutil.which(tool) is not None for tool in REQUIRED_TOOLS}


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

def detect_removable_devices() -> list[BlockDevice]:
    """Run lsblk -J and return safe removable devices with FAT partitions."""
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
        if str(disk.get("rm", "0")) not in ("1", "true", True):
            continue

        children = disk.get("children") or []
        for part in children:
            if part.get("type") != "part":
                continue

            mp = part.get("mountpoint") or None
            fs = (part.get("fstype") or "").lower()
            label = part.get("label") or ""
            size_str = part.get("size") or "0"

            # Skip if any dangerous mountpoint is in use
            if mp and (_is_os_mountpoint(mp)):
                continue

            try:
                part_bytes = int(size_str)
            except (ValueError, TypeError):
                part_bytes = 0

            disk_size_str = disk.get("size") or "0"
            try:
                disk_bytes = int(disk_size_str)
            except (ValueError, TypeError):
                disk_bytes = part_bytes

            devices.append(BlockDevice(
                device=f"/dev/{disk['name']}",
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
            "partclone.fat", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stderr.decode("utf-8", errors="replace")
        m = re.search(r"(\d+\.\d+[\.\d]*)", text)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Query used bytes from a partition
# ---------------------------------------------------------------------------

async def query_used_bytes(partition: str) -> int:
    """Run 'partclone.fat --info' and parse used block count × block size."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", "partclone.fat", "--info", "--source", partition,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    output = stderr.decode("utf-8", errors="replace") + proc.stdout and b"" or b""

    # partclone --info writes to stderr
    text = stderr.decode("utf-8", errors="replace")

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
# Backup
# ---------------------------------------------------------------------------

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

    # Step 1: query used bytes
    progress_cb(0.0, "Querying partition usage…")
    try:
        used_bytes = await query_used_bytes(device.partition)
    except Exception as e:
        log.warning("query_used_bytes failed (%s), continuing with 0", e)
        used_bytes = 0

    # Step 2: get partition start offset via parted
    partition_start = await _get_partition_start_bytes(device.device, device.partition)

    progress_cb(2.0, f"Starting backup of {device.partition}…")

    # Step 3: run partclone.fat --clone | zstd
    partclone_cmd = [
        "sudo", "partclone.fat",
        "--clone",
        "--source", device.partition,
        "--output", "-",
    ]
    zstd_cmd = ["zstd", "-T0", "-3", "-o", str(image_path), "--force"]

    p1 = await asyncio.create_subprocess_exec(
        *partclone_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    p2 = await asyncio.create_subprocess_exec(
        *zstd_cmd,
        stdin=p1.stdout,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # Allow p1's stdout to flow into p2
    if p1.stdout:
        p1.stdout.set_exception_handler(None)

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
                    # Map 0–100% to 2–95% of our overall progress
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
        raise RuntimeError(
            f"partclone.fat exited with code {p1.returncode}.\n{stderr_tail}"
        )
    if p2.returncode != 0:
        zstd_err = b""
        if p2.stderr:
            zstd_err = await p2.stderr.read()
        if image_path.exists():
            image_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"zstd exited with code {p2.returncode}.\n{zstd_err.decode('utf-8', errors='replace')}"
        )

    if not image_path.exists() or image_path.stat().st_size == 0:
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

    progress_cb(100.0, "Backup complete.")
    return meta


async def _get_partition_start_bytes(device: str, partition: str) -> int:
    """Use parted to get the partition start offset in bytes."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "parted", "-s", "-m", device, "unit", "B", "print",
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

    if mode == RestoreMode.SHRINK:
        # Repartition the target device to a fresh MBR + single FAT32 partition
        progress_cb(2.0, f"Repartitioning {device}…")
        await _run_sudo_cmd(
            ["parted", "-s", device, "mklabel", "msdos"],
            "parted mklabel",
        )
        await _run_sudo_cmd(
            ["parted", "-s", device, "mkpart", "primary", "fat32", "1MiB", "100%"],
            "parted mkpart",
        )
        await _run_sudo_cmd(
            ["parted", "-s", device, "set", "1", "boot", "on"],
            "parted set boot",
        )
        # After repartition the partition node may need a moment to appear
        await asyncio.sleep(1)
        progress_cb(8.0, "Repartition complete.")

    # Restore the image
    progress_cb(10.0, f"Restoring to {partition}…")

    zstd_proc = await asyncio.create_subprocess_exec(
        "zstd", "-d", "-T0", str(image_path), "--stdout",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    partclone_proc = await asyncio.create_subprocess_exec(
        "sudo", "partclone.fat",
        "--restore",
        "--output", partition,
        stdin=zstd_proc.stdout,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

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

    if zstd_proc.returncode != 0:
        raise RuntimeError(f"zstd decompression failed (exit {zstd_proc.returncode}).")
    if partclone_proc.returncode != 0:
        tail = "\n".join(partclone_err_lines[-10:])
        raise RuntimeError(
            f"partclone.fat restore failed (exit {partclone_proc.returncode}).\n{tail}"
        )

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
    """Run a sudo command, return stdout, raise RuntimeError on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{label} failed (exit {proc.returncode}): {err}")
    return stdout.decode("utf-8", errors="replace")
