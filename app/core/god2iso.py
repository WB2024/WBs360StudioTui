"""god2iso binary manager and async conversion wrapper.

god2iso converts a Games on Demand (GOD) package back to a standard Xbox 360
ISO file.  It is the reverse of iso2god.

The binary is NOT bundled — the user must supply it via the god2iso_binary_path
setting (Settings → X360Forge Tools).

Usage: god2iso <god_header_file> <output_dir>
  god_header_file — the container file (no extension, e.g. the file that sits
                    directly inside the TitleID folder in the GOD structure).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from platformdirs import user_data_dir

from app.core.constants import APP_NAME

log = logging.getLogger(__name__)

GOD2ISO_VERSION = "v1.0.0"


def _tools_dir() -> Path:
    p = Path(user_data_dir(APP_NAME)) / "tools"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _binary_name() -> str:
    return "god2iso.exe" if sys.platform == "win32" else "god2iso"


def binary_path(custom_path: str = "") -> Path:
    """Return the path to the god2iso binary.

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


class God2IsoError(Exception):
    pass


async def convert_god(
    god_file: str | Path,
    output_dir: str | Path,
    binary: str | Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Convert a GOD container file to an Xbox 360 ISO.

    Args:
        god_file:   Path to the GOD container/header file (no extension).
        output_dir: Directory where the output ISO will be written.
        binary:     Path to the god2iso binary.
        on_line:    Optional callback receiving each output line.

    Raises God2IsoError on non-zero exit code.
    """
    cmd = [str(binary), str(god_file), str(output_dir)]
    log.info("god2iso: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        log.debug("god2iso: %s", line)
        if on_line:
            on_line(line)

    await proc.wait()
    if proc.returncode != 0:
        raise God2IsoError(
            f"god2iso exited with code {proc.returncode}"
        )
