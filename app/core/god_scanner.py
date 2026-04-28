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

        for title_dir in sorted(game_dir.iterdir()):
            if not title_dir.is_dir():
                continue
            title_id = title_dir.name
            # Must be exactly 8 hex characters
            if len(title_id) != 8:
                continue
            try:
                int(title_id, 16)
            except ValueError:
                continue

            for ct_dir in sorted(title_dir.iterdir()):
                if not ct_dir.is_dir():
                    continue
                content_type = ct_dir.name

                # Find the CON container file (a file with no extension, not a .data folder)
                container_file: str | None = None
                for entry in ct_dir.iterdir():
                    if entry.is_file() and not entry.suffix:
                        container_file = entry.name
                        break

                if container_file is None:
                    log.debug("No container file found in %s — skipping", ct_dir)
                    continue

                data_folder = ct_dir / (container_file + ".data")
                if not data_folder.is_dir():
                    log.debug("No .data folder for %s — skipping", container_file)
                    continue

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

    log.info("GOD scan found %d game(s) in %s", len(games), root)
    return games
