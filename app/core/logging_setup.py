"""Logging setup — file-based, never user-facing."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config.settings import log_dir
from app.core.constants import APP_NAME


def setup_logging(level: int = logging.INFO) -> None:
    log_file = log_dir() / f"{APP_NAME}.log"
    handler = RotatingFileHandler(str(log_file), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    # Replace any existing handlers
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
