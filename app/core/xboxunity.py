"""XboxUnity.net API client — Title Update search and download."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.constants import HTTP_TIMEOUT, XBOXUNITY_TU_DOWNLOAD, XBOXUNITY_TU_INFO, XBOXUNITY_TITLE_LIST

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class XboxUnityTitle:
    """A game entry returned by the XboxUnity TitleList search."""
    title_id: str
    name: str
    title_type: str      # e.g. "360"
    update_count: int


@dataclass
class XboxUnityTuEntry:
    """A single Title Update entry from XboxUnity, tied to a specific Media ID."""
    tuid: str           # XboxUnity internal TU ID
    version: str        # "1", "2", etc.
    media_id: str       # 8-char hex disc variant identifier
    name: str           # Game display name from XboxUnity
    size_kb: int        # File size in KB
    upload_date: str    # "YYYY-MM-DD"
    sha1_hash: str
    base_version: str   # Hex base version from STFS header

    @property
    def size_str(self) -> str:
        if self.size_kb < 1024:
            return f"{self.size_kb} KB"
        return f"{self.size_kb / 1024:.1f} MB"

    @property
    def version_str(self) -> str:
        try:
            return f"TU{int(self.version)}"
        except (ValueError, TypeError):
            return self.version or "?"

    @property
    def download_url(self) -> str:
        return f"{XBOXUNITY_TU_DOWNLOAD}?tuid={self.tuid}"


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------

async def search_titles(query: str, count: int = 30) -> list[XboxUnityTitle]:
    """Search XboxUnity for games by name or Title ID.

    Only returns titles that have at least one Title Update available.
    """
    url = (
        f"{XBOXUNITY_TITLE_LIST}"
        f"?page=0&count={count}&search={query}"
        f"&sort=3&direction=1&category=0&filter=0"
    )
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    return [
        XboxUnityTitle(
            title_id=item["TitleID"],
            name=item["Name"],
            title_type=item.get("TitleType", "360"),
            update_count=int(item.get("Updates", 0)),
        )
        for item in data.get("Items", [])
    ]


async def get_title_updates(title_id: str) -> list[XboxUnityTuEntry]:
    """Return all Title Update entries for *title_id*, flattened across all Media IDs.

    Results are sorted by Media ID then version (ascending).
    """
    url = f"{XBOXUNITY_TU_INFO}?titleid={title_id}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    entries: list[XboxUnityTuEntry] = []
    for media_block in data.get("MediaIDS", []):
        media_id = media_block.get("MediaID", "")
        for tu in media_block.get("Updates", []):
            entries.append(XboxUnityTuEntry(
                tuid=str(tu.get("TitleUpdateID", "")),
                version=str(tu.get("Version", "")),
                media_id=media_id,
                name=tu.get("Name", ""),
                size_kb=int(tu.get("Size", 0)),
                upload_date=(tu.get("UploadDate") or "")[:10],
                sha1_hash=tu.get("hash", ""),
                base_version=tu.get("BaseVersion", ""),
            ))

    entries.sort(key=lambda e: (e.media_id, e.version.zfill(4)))
    return entries


async def download_title_update(
    entry: XboxUnityTuEntry,
    dest_dir: Path,
    title_id: str,
    progress_callback=None,  # (bytes_done: int, total_bytes: int) -> None
) -> Path:
    """Download a Title Update STFS package from XboxUnity to *dest_dir*.

    The local filename is ``{TitleID}_{version_str}_{tuid}`` (no extension),
    matching the convention of real STFS packages.

    Returns the path of the downloaded file.
    Raises on HTTP errors or IO failures.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{title_id}_{entry.version_str}_{entry.tuid}"
    dest = dest_dir / filename
    tmp = dest.with_suffix(".part")

    try:
        # Use no timeout for the actual download — TUs can be several MB
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", entry.download_url) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"XboxUnity returned HTTP {resp.status_code} for TU {entry.tuid}"
                    )
                total = int(resp.headers.get("Content-Length") or 0)
                received = 0
                with tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        received += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(received, total)
                            except Exception:
                                pass
        tmp.replace(dest)
        log.info("Downloaded TU %s → %s", entry.tuid, dest)
        return dest
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
