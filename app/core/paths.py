"""Path utilities — Xbox path normalization and resolution."""
from __future__ import annotations

import os
from pathlib import PurePosixPath

from app.core.constants import XBOX_DRIVE_PREFIXES


def normalize_xbox_path(path: str) -> str:
    """Convert Windows backslashes to forward slashes for FTP, and collapse double slashes."""
    if not path:
        return path
    result = path.replace("\\", "/")
    # Collapse consecutive slashes (e.g. from {AURORAPATH}/ + \subfolder)
    while "//" in result:
        result = result.replace("//", "/")
    return result


def is_directory_path(path: str) -> bool:
    """True if path looks like a directory (ends with separator OR last segment has no extension)."""
    if not path:
        return True
    norm = normalize_xbox_path(path)
    if norm.endswith("/"):
        return True
    last = norm.rsplit("/", 1)[-1]
    # No '.' in last segment → treat as directory
    return "." not in last


def join_xbox_path(install_path: str, filename: str) -> str:
    """Append filename to install_path if it's a directory; otherwise use install_path as-is."""
    norm = normalize_xbox_path(install_path)
    if is_directory_path(norm):
        if not norm.endswith("/"):
            norm += "/"
        return norm + filename
    return norm


def split_xbox_drive(path: str) -> tuple[str, str]:
    """Split 'Hdd1:/Content/foo' → ('Hdd1:', '/Content/foo'). Empty drive if none."""
    norm = normalize_xbox_path(path)
    for prefix in XBOX_DRIVE_PREFIXES:
        if norm.lower().startswith(prefix.lower()):
            rest = norm[len(prefix):]
            if not rest.startswith("/"):
                rest = "/" + rest
            return prefix, rest
    return "", norm if norm.startswith("/") else "/" + norm


def map_xbox_path_to_usb(xbox_path: str, usb_root: str) -> str:
    """Strip drive prefix; place under usb_root using OS-native separators."""
    _, rest = split_xbox_drive(xbox_path)
    rest = rest.lstrip("/")
    parts = rest.split("/")
    return os.path.join(usb_root, *parts)


def parent_dir(remote_path: str) -> str:
    """Posix-style parent of an Xbox FTP path."""
    norm = normalize_xbox_path(remote_path)
    if norm.endswith("/"):
        norm = norm[:-1]
    parent = str(PurePosixPath(norm).parent)
    return parent if parent != "." else "/"
