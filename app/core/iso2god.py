"""iso2god binary manager and async conversion wrapper.

The iso2god.exe binary (https://github.com/iliazeus/iso2god-rs) is stored in
the user's app data directory and downloaded on first use.

Conversion output format (stdout lines we parse for progress):
    extracting ISO metadata
    Title ID: XXXXXXXX
        Name: Game Title
        Type: Games on Demand
    clearing data directory
    writing part files:  0/42
    writing part files:  1/42
    ...
    calculating MHT hash chain
    writing con header
    done
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional



log = logging.getLogger(__name__)

# ----- Binary location & download -----------------------------------------

ISO2GOD_VERSION = "v1.8.1"
_WINDOWS_URL = f"https://github.com/iliazeus/iso2god-rs/releases/download/{ISO2GOD_VERSION}/iso2god-x86_64-windows.exe"
_LINUX_URL = f"https://github.com/iliazeus/iso2god-rs/releases/download/{ISO2GOD_VERSION}/iso2god-x86_64-linux"
_MACOS_URL = f"https://github.com/iliazeus/iso2god-rs/releases/download/{ISO2GOD_VERSION}/iso2god-x86_64-macos"


def _tools_dir() -> Path:
    # Bundled tools shipped with the repo: <repo_root>/tools/
    return Path(__file__).resolve().parent.parent.parent / "tools"


def _binary_name() -> str:
    if sys.platform == "win32":
        return "iso2god.exe"
    return "iso2god"


def _download_url() -> str:
    if sys.platform == "win32":
        return _WINDOWS_URL
    if sys.platform == "darwin":
        return _MACOS_URL
    return _LINUX_URL


def binary_path(custom_path: str = "") -> Path:
    """Return the path to the iso2god binary.

    If *custom_path* is set (from settings) and the file exists, use it.
    Otherwise fall back to the managed copy in the app data tools folder.
    """
    if custom_path:
        p = Path(custom_path)
        if p.is_file():
            return p
    return _tools_dir() / _binary_name()


def binary_exists(custom_path: str = "") -> bool:
    return binary_path(custom_path).is_file()


async def download_binary(
    custom_path: str = "",
    progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Download the iso2god binary if not already present. Returns its path."""
    dest = binary_path(custom_path)
    if dest.is_file():
        return dest

    import httpx

    url = _download_url()
    log.info("Downloading iso2god binary from %s → %s", url, dest)

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            received = 0
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, total)

    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | 0o111)

    log.info("iso2god binary ready at %s", dest)
    return dest


# ----- Progress parsing -----------------------------------------------------

_PART_RE = re.compile(r"writing part files:\s*(\d+)/(\d+)")
_TITLE_RE = re.compile(r"Title ID:\s*([0-9A-Fa-f]{8})")
_NAME_RE = re.compile(r"Name:\s*(.+)")


@dataclass
class ConversionProgress:
    stage: str = "initializing"
    parts_done: int = 0
    parts_total: int = 0
    title_id: str = ""
    game_name: str = ""


# ----- Async conversion -----------------------------------------------------

class Iso2GodError(Exception):
    pass


async def convert_iso(
    iso_path: str | Path,
    dest_dir: str | Path,
    binary: str | Path,
    *,
    num_threads: int = 1,
    trim: bool = True,
    game_title: Optional[str] = None,
    on_progress: Optional[Callable[[ConversionProgress], None]] = None,
) -> ConversionProgress:
    """Run iso2god and stream progress. Returns final ConversionProgress on success.

    Raises Iso2GodError on non-zero exit.
    """
    cmd: list[str] = [
        str(binary),
        str(iso_path),
        str(dest_dir),
        f"-j{num_threads}",
    ]
    if trim:
        cmd.append("--trim")
    if game_title:
        cmd.extend(["--game-title", game_title])

    log.info("Running: %s", " ".join(cmd))

    prog = ConversionProgress()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        log.debug("iso2god: %s", line)

        if m := _PART_RE.search(line):
            prog.parts_done = int(m.group(1))
            prog.parts_total = int(m.group(2))
            prog.stage = "writing"
        elif m := _TITLE_RE.search(line):
            prog.title_id = m.group(1)
        elif m := _NAME_RE.search(line):
            prog.game_name = m.group(1).strip()
        elif "extracting" in line:
            prog.stage = "reading ISO"
        elif "clearing" in line:
            prog.stage = "clearing output"
        elif "MHT" in line:
            prog.stage = "hashing"
        elif "con header" in line:
            prog.stage = "writing header"
        elif line.strip() == "done":
            prog.stage = "done"

        if on_progress:
            on_progress(prog)

    await proc.wait()
    if proc.returncode != 0:
        raise Iso2GodError(f"iso2god exited with code {proc.returncode}")

    prog.stage = "done"
    return prog


async def dry_run(
    iso_path: str | Path,
    binary: str | Path,
) -> ConversionProgress:
    """Run iso2god --dry-run to extract title info without converting."""
    import tempfile
    tmp_dest = Path(tempfile.mkdtemp(prefix="x360tm-dryrun-"))
    cmd = [str(binary), "--dry-run", str(iso_path), str(tmp_dest)]
    prog = ConversionProgress(stage="probing")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if m := _TITLE_RE.search(line):
            prog.title_id = m.group(1)
        elif m := _NAME_RE.search(line):
            prog.game_name = m.group(1).strip()

    await proc.wait()
    try:
        import shutil
        shutil.rmtree(tmp_dest, ignore_errors=True)
    except Exception:
        pass
    return prog
