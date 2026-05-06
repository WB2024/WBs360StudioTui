"""abgx360 binary manager and async ISO verification/fix wrapper.

abgx360 (http://abgx360.net) verifies and patches Xbox 360 ISO images to
ensure they have valid stealth data, correct topology data, and pass online
verification checks.

Flags used:  --af3 -p -s -o
  --af3   AutoFix mode 3 — fix everything automatically
  -p      Patch topology data
  -s      Stealth check
  -o      Online verification

The binary is NOT bundled — the user must supply it via the abgx360_binary_path
setting (Settings → X360Forge Tools).
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

ABGX360_VERSION = "v1.0.6"


def _tools_dir() -> Path:
    p = Path(user_data_dir(APP_NAME)) / "tools"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _binary_name() -> str:
    return "abgx360.exe" if sys.platform == "win32" else "abgx360"


def binary_path(custom_path: str = "") -> Path:
    """Return the path to the abgx360 binary.

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


class Abgx360Error(Exception):
    pass


async def fix_iso(
    iso_path: str | Path,
    binary: str | Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Run abgx360 --af3 -p -s -o on *iso_path*.

    Streams each stdout/stderr line to *on_line* if provided.
    Raises Abgx360Error on non-zero exit.
    """
    cmd = [str(binary), "--af3", "-p", "-s", "-o", "--", str(iso_path)]
    log.info("abgx360: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        log.debug("abgx360: %s", line)
        if on_line:
            on_line(line)

    await proc.wait()
    if proc.returncode != 0:
        raise Abgx360Error(
            f"abgx360 exited with code {proc.returncode}"
        )
