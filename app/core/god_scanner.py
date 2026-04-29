"""Scanner for locally-stored GOD (Games on Demand) format games.

Expected directory structure:
    {local_god_path}/
        {GameName}/
            {TitleID}/          <- 8-char hex string
                {ContentType}/  <- e.g. 00007000
                    {Hash}      <- CON container file (no extension)
                    {Hash}.data/
                        Data0000
                        Data0001
                        ...
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.models.god_game import GodGameItem

log = logging.getLogger(__name__)


def scan_god_path(path: str | Path) -> list[GodGameItem]:
    """Scan *path* for GOD-format games and return a list of :class:`GodGameItem`.

    Returns an empty list if the path does not exist or is empty.
    """
    root = Path(path) if path else None
    if not root or not root.is_dir():
        return []

    games: list[GodGameItem] = []

    for game_dir in sorted(root.iterdir()):
        if not game_dir.is_dir():
            continue
        game_name = game_dir.name
        _scan_for_title_ids(game_dir, game_name, games)

    log.info("GOD scan found %d game(s) in %s", len(games), root)
    return games


def _is_title_id(name: str) -> bool:
    """Return True if *name* is exactly 8 hex characters."""
    if len(name) != 8:
        return False
    try:
        int(name, 16)
        return True
    except ValueError:
        return False


def _scan_for_title_ids(directory: Path, game_name: str, games: list[GodGameItem]) -> None:
    """Recursively walk *directory* looking for Title ID sub-folders.

    This handles both the flat layout ({game}/{TitleID}/) and layouts with an
    extra intermediate folder ({game}/{SubFolder}/{TitleID}/).
    """
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        if _is_title_id(entry.name):
            _collect_game(entry, game_name, games)
        else:
            # One extra level of nesting before the Title ID is tolerated.
            _scan_for_title_ids(entry, game_name, games)


def _collect_game(title_dir: Path, game_name: str, games: list[GodGameItem]) -> None:
    """Look for content-type sub-folders inside a Title ID folder and collect games."""
    title_id = title_dir.name
    for ct_dir in sorted(title_dir.iterdir()):
        if not ct_dir.is_dir():
            continue
        content_type = ct_dir.name

        # Find the CON container file: a file with no extension inside ct_dir.
        container_file: str | None = None
        for entry in ct_dir.iterdir():
            if entry.is_file() and not entry.suffix:
                container_file = entry.name
                break

        if container_file is None:
            log.debug("No container file found in %s — skipping", ct_dir)
            continue

        # The .data directory is optional (single-file GOD containers lack it).
        data_folder = ct_dir / (container_file + ".data")
        if not data_folder.exists():
            log.debug(
                "No .data folder for %s in %s — treating as single-file container",
                container_file, ct_dir,
            )

        games.append(
            GodGameItem(
                name=game_name,
                title_id=title_id,
                content_type=content_type,
                local_path=ct_dir,
                container_file=container_file,
            )
        )
        log.debug("Found GOD game: %s (%s)", game_name, title_id)
