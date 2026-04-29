"""Core logic for analysing and reorganising the Xbox 360 games directory over FTP.

Expected games-root layout on the console (one of several mixed patterns):

  games_root/TitleID/                          ← bare TitleID (flat)
  games_root/FriendlyName/TitleID/             ← nested (preferred)
  games_root/FriendlyName - TitleID/TitleID/   ← combined-name parent
  games_root/TitleID - FriendlyName/TitleID/   ← reversed combined-name parent
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.ftp_client import FtpClient

log = logging.getLogger(__name__)

TITLE_ID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")

# ── Format identifiers ───────────────────────────────────────────────────────
FORMAT_TITLE_ID = "TitleID"
FORMAT_NAME_SLASH_TITLE_ID = "Name/TitleID"
FORMAT_NAME_DASH_TITLE_ID = "Name - TitleID"
FORMAT_TITLE_ID_DASH_NAME = "TitleID - Name"

ALL_FORMATS = [
    FORMAT_TITLE_ID,
    FORMAT_NAME_SLASH_TITLE_ID,
    FORMAT_NAME_DASH_TITLE_ID,
    FORMAT_TITLE_ID_DASH_NAME,
]

# Minimum fuzzy-match ratio to accept an automatic title-ID assignment.
FUZZY_THRESHOLD = 0.55


def is_title_id(name: str) -> bool:
    return bool(TITLE_ID_RE.match(name))


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class GameDirEntry:
    """Analysed representation of a single game's directory on the console."""

    title_id: str | None            # 8-char hex uppercase, or None if unknown
    friendly_name: str | None       # Human-readable name from CSV, or None
    folder_label: str               # The top-level directory name in games root
    current_tid_ftp: str            # Absolute FTP path to the TitleID folder
    current_parent_ftp: str | None  # Absolute FTP path to parent folder (if nested)
    fuzzy_confidence: float = 1.0
    # "csv_id"     — matched by TitleID exactly in the CSV
    # "csv_fuzzy"  — fuzzy-matched folder name to a CSV title
    # "structure"  — TitleID found in directory structure, no CSV name found
    # "unknown"    — could not identify
    match_source: str = "unknown"


@dataclass
class TidyMove:
    """A planned (and later executed) reorganisation action for one game."""

    entry: GameDirEntry
    from_ftp: str           # Current absolute FTP path to the TitleID folder
    to_ftp: str             # Target absolute FTP path for the TitleID folder
    mkdir_ftp: str | None   # Create this parent directory before moving
    rmdir_ftp: str | None   # Try to remove this old parent after moving (if empty)
    description: str        # Human-readable summary shown in the preview table
    skipped: bool = False   # True → no FTP action needed or possible
    result: str = ""        # Filled in after apply: "ok" / "error: …" / "skipped"


# ── CSV helpers ──────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    """Strip region tags and non-alphanumeric chars for fuzzy comparison."""
    s = s.lower()
    s = re.sub(r"[\(\[][^)\]]*[\)\]]", "", s)   # remove (region) [tags]
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fuzzy_find(
    folder_name: str,
    csv_titles: dict[str, str],
) -> tuple[str | None, str | None, float]:
    """Return *(title_id, title_name, confidence)* for the best fuzzy match."""
    needle = _normalise(folder_name)
    if not needle:
        return None, None, 0.0
    best_score, best_tid, best_name = 0.0, None, None
    for tid, name in csv_titles.items():
        score = difflib.SequenceMatcher(None, needle, _normalise(name)).ratio()
        if score > best_score:
            best_score, best_tid, best_name = score, tid, name
    return best_tid, best_name, best_score


# ── FTP analysis ─────────────────────────────────────────────────────────────

async def analyse_games_root(
    client: "FtpClient",
    games_root_ftp: str,
    csv_titles: dict[str, str],
    progress_cb=None,
) -> list[GameDirEntry]:
    """List the games root over FTP and classify each top-level entry.

    Detection rules (in order):
    1. If the folder name is a valid 8-hex TitleID → bare TitleID structure.
    2. If the folder contains any 8-hex sub-folder → Name/TitleID structure.
    3. Otherwise → fuzzy-match the folder name to the CSV to find the TitleID.
    """
    entries: list[GameDirEntry] = []
    root = games_root_ftp.rstrip("/")

    if progress_cb:
        progress_cb("Listing games directory…")

    try:
        top_level = await client.list_detail(root)
    except Exception as exc:
        log.error("Failed listing %s: %s", root, exc)
        raise

    for name, is_dir, _size, _mod in top_level:
        if not is_dir or not name or name in (".", ".."):
            continue

        entry_ftp = f"{root}/{name}"

        if is_title_id(name):
            # ── Bare TitleID at games root ───────────────────────────────
            tid = name.upper()
            friendly = csv_titles.get(tid)
            entries.append(GameDirEntry(
                title_id=tid,
                friendly_name=friendly,
                folder_label=name,
                current_tid_ftp=entry_ftp,
                current_parent_ftp=None,
                fuzzy_confidence=1.0,
                match_source="csv_id" if friendly else "structure",
            ))

        else:
            # ── Look inside for TitleID sub-folders ──────────────────────
            if progress_cb:
                progress_cb(f"Analysing {name}…")

            try:
                sub = await client.list_detail(entry_ftp)
            except Exception:
                sub = []

            tid_subs = [(e[0], e[0].upper()) for e in sub if e[1] and is_title_id(e[0])]

            if tid_subs:
                # Name/TitleID structure (one or more TitleID sub-folders)
                for raw_tid, tid in tid_subs:
                    friendly = csv_titles.get(tid)
                    entries.append(GameDirEntry(
                        title_id=tid,
                        friendly_name=friendly or name,
                        folder_label=name,
                        current_tid_ftp=f"{entry_ftp}/{raw_tid}",
                        current_parent_ftp=entry_ftp,
                        fuzzy_confidence=1.0,
                        match_source="csv_id" if friendly else "structure",
                    ))
            else:
                # No TitleID sub-folder — try fuzzy-matching the folder name
                tid, friendly, score = fuzzy_find(name, csv_titles)
                entries.append(GameDirEntry(
                    title_id=tid if score >= FUZZY_THRESHOLD else None,
                    friendly_name=friendly if score >= FUZZY_THRESHOLD else None,
                    folder_label=name,
                    current_tid_ftp=entry_ftp,
                    current_parent_ftp=None,
                    fuzzy_confidence=score,
                    match_source="csv_fuzzy" if score >= FUZZY_THRESHOLD else "unknown",
                ))

    log.info("Analysed %d entries in %s", len(entries), root)
    return entries


# ── Plan generation ──────────────────────────────────────────────────────────

def _safe_name(s: str) -> str:
    """Strip characters that are unsafe in Xbox/FAT32 folder names."""
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def build_plan(
    entries: list[GameDirEntry],
    fmt: str,
    games_root_ftp: str,
) -> list[TidyMove]:
    """Compute the list of moves required to achieve the chosen *fmt*."""
    root = games_root_ftp.rstrip("/")
    moves: list[TidyMove] = []

    for entry in entries:
        if entry.title_id is None:
            moves.append(TidyMove(
                entry=entry,
                from_ftp=entry.current_tid_ftp,
                to_ftp=entry.current_tid_ftp,
                mkdir_ftp=None,
                rmdir_ftp=None,
                description="Skip — Title ID unknown",
                skipped=True,
            ))
            continue

        tid = entry.title_id
        label = _safe_name(entry.friendly_name or entry.folder_label)

        if fmt == FORMAT_TITLE_ID:
            target = f"{root}/{tid}"
            mkdir_ftp = None
            rmdir_ftp = entry.current_parent_ftp

        elif fmt == FORMAT_NAME_SLASH_TITLE_ID:
            parent = f"{root}/{label}"
            target = f"{parent}/{tid}"
            mkdir_ftp = parent
            rmdir_ftp = (
                entry.current_parent_ftp
                if entry.current_parent_ftp
                and entry.current_parent_ftp.lower() != parent.lower()
                else None
            )

        elif fmt == FORMAT_NAME_DASH_TITLE_ID:
            parent = f"{root}/{label} - {tid}"
            target = f"{parent}/{tid}"
            mkdir_ftp = parent
            rmdir_ftp = (
                entry.current_parent_ftp
                if entry.current_parent_ftp
                and entry.current_parent_ftp.lower() != parent.lower()
                else None
            )

        elif fmt == FORMAT_TITLE_ID_DASH_NAME:
            parent = f"{root}/{tid} - {label}"
            target = f"{parent}/{tid}"
            mkdir_ftp = parent
            rmdir_ftp = (
                entry.current_parent_ftp
                if entry.current_parent_ftp
                and entry.current_parent_ftp.lower() != parent.lower()
                else None
            )

        else:
            continue

        already_correct = entry.current_tid_ftp.lower() == target.lower()

        # Relative target path (for display — strip games root prefix)
        rel_target = target[len(root) + 1:]

        moves.append(TidyMove(
            entry=entry,
            from_ftp=entry.current_tid_ftp,
            to_ftp=target,
            mkdir_ftp=mkdir_ftp if not already_correct else None,
            rmdir_ftp=rmdir_ftp if not already_correct else None,
            description="Already correct" if already_correct else f"-> {rel_target}",
            skipped=already_correct,
        ))

    return moves


# ── Apply ────────────────────────────────────────────────────────────────────

async def apply_moves(
    client: "FtpClient",
    moves: list[TidyMove],
    progress_cb=None,
) -> list[tuple[TidyMove, bool, str]]:
    """Execute planned moves over FTP.

    Returns a list of *(move, success, message)* tuples for moves that were
    attempted (i.e. not skipped).
    """
    results: list[tuple[TidyMove, bool, str]] = []
    to_do = [m for m in moves if not m.skipped]
    parents_to_remove: set[str] = set()

    for i, move in enumerate(to_do):
        if progress_cb:
            progress_cb(f"[{i + 1}/{len(to_do)}] Moving {move.entry.folder_label}…")
        try:
            if move.mkdir_ftp:
                await client.make_directory(move.mkdir_ftp)
            await client.rename(move.from_ftp, move.to_ftp)
            if move.rmdir_ftp:
                parents_to_remove.add(move.rmdir_ftp)
            results.append((move, True, "OK"))
        except Exception as exc:
            log.error("Move failed for %s: %s", move.entry.folder_label, exc)
            results.append((move, False, str(exc)))

    # Best-effort removal of now-empty parent directories.
    for parent in parents_to_remove:
        try:
            await client.remove_directory(parent)
        except Exception:
            pass  # Not empty yet, or already removed — safe to ignore.

    return results
