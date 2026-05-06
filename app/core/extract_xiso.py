"""extract-xiso binary manager and async wrappers.

extract-xiso (https://github.com/XboxDev/extract-xiso) is used to:
  - Extract an Xbox 360 ISO to a game folder:  extract-xiso -x game.iso
  - Create an Xbox 360 ISO from a game folder: extract-xiso -c GameFolder/ game.iso

The binary is NOT bundled with x360tm.  The user must supply it via the
extract_xiso_binary_path setting (Settings → X360Forge Tools).  Once a CI
pipeline publishes pre-built binaries on GitHub releases, auto-download will
be added here following the same pattern as iso2god.py.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional



log = logging.getLogger(__name__)

EXTRACT_XISO_VERSION = "v2.7.1"


def _tools_dir() -> Path:
    # Bundled tools shipped with the repo: <repo_root>/tools/
    return Path(__file__).resolve().parent.parent.parent / "tools"


def _binary_name() -> str:
    return "extract-xiso.exe" if sys.platform == "win32" else "extract-xiso"


def binary_path(custom_path: str = "") -> Path:
    """Return the path to the extract-xiso binary.

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


class ExtractXisoError(Exception):
    pass


async def extract_iso(
    iso_path: str | Path,
    output_dir: str | Path,
    binary: str | Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Extract an Xbox 360 ISO file (extract-xiso -x) into *output_dir*.

    The binary is run with *output_dir* as the working directory so that the
    extracted folder lands there directly.

    Streams each stdout/stderr line to *on_line* if provided.
    Raises ExtractXisoError on non-zero exit.
    """
    cmd = [str(binary), "-x", str(iso_path)]
    log.info("extract-xiso extract: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(output_dir),
    )
    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        log.debug("extract-xiso: %s", line)
        if on_line:
            on_line(line)

    await proc.wait()
    if proc.returncode != 0:
        raise ExtractXisoError(
            f"extract-xiso exited with code {proc.returncode}"
        )


async def create_iso(
    game_dir: str | Path,
    output_iso: str | Path,
    binary: str | Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Create an Xbox 360 ISO from a game folder (extract-xiso -c).

    Args:
        game_dir:   Path to the folder containing the game files.
        output_iso: Full path for the output .iso file.
        binary:     Path to the extract-xiso binary.
        on_line:    Optional callback receiving each output line.

    Raises ExtractXisoError on non-zero exit.
    """
    cmd = [str(binary), "-c", str(game_dir), str(output_iso)]
    log.info("extract-xiso create: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        log.debug("extract-xiso: %s", line)
        if on_line:
            on_line(line)

    await proc.wait()
    if proc.returncode != 0:
        raise ExtractXisoError(
            f"extract-xiso exited with code {proc.returncode}"
        )
