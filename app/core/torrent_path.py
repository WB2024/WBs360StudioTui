"""Resolve and validate the download save_path for a torrent."""
from __future__ import annotations

import os
from pathlib import Path


class DownloadPathError(Exception):
    pass


def resolve_save_path(
    runtime: str | None = None,
    config: str | None = None,
    create: bool = True,
) -> str | None:
    """Resolve the save path (precedence: runtime → config → None=client default).

    If a path is provided it is normalised to absolute. If `create` is True the
    directory is created on demand. Always validated for writability.

    Returns the absolute path string, or None to mean "use qBittorrent default".

    Raises:
        DownloadPathError: path is unwritable or could not be created
    """
    raw = (runtime or "").strip() or (config or "").strip() or ""
    if not raw:
        return None

    p = Path(raw).expanduser().resolve()
    if not p.exists():
        if create:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                raise DownloadPathError(
                    f"Could not create save path '{p}': {exc}"
                ) from exc
        else:
            raise DownloadPathError(f"Save path does not exist: {p}")

    if not p.is_dir():
        raise DownloadPathError(f"Save path is not a directory: {p}")

    if not os.access(p, os.W_OK):
        raise DownloadPathError(f"Save path is not writable: {p}")

    return str(p)
