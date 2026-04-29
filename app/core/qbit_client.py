"""qBittorrent Web API wrapper.

Implements the §5.5 add-torrent flow:
  1. Add .torrent paused (with optional save_path)
  2. Wait for torrent to register
  3. Set ALL files priority 0 (skip)
  4. Set selected files priority 1 (normal)
  5. Resume

Uses qbittorrent-api. All network calls are wrapped in asyncio.to_thread to
keep the TUI responsive.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Iterable

import qbittorrentapi

log = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────────

class QbitError(Exception):
    pass


class QbitConnectionError(QbitError):
    pass


class QbitAddError(QbitError):
    pass


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class QbitConfig:
    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "adminadmin"


# ── Client wrapper ────────────────────────────────────────────────────────────

class QbitClient:
    """Async-friendly wrapper around qbittorrent-api."""

    def __init__(self, cfg: QbitConfig) -> None:
        self.cfg = cfg
        self._client: qbittorrentapi.Client | None = None

    # ── connection ──
    async def connect(self) -> None:
        def _do() -> qbittorrentapi.Client:
            c = qbittorrentapi.Client(
                host=self.cfg.host,
                port=self.cfg.port,
                username=self.cfg.username,
                password=self.cfg.password,
                REQUESTS_ARGS={"timeout": (5, 15)},
            )
            c.auth_log_in()
            return c

        try:
            self._client = await asyncio.to_thread(_do)
        except qbittorrentapi.LoginFailed as exc:
            raise QbitConnectionError(f"Login failed: {exc}") from exc
        except qbittorrentapi.APIConnectionError as exc:
            raise QbitConnectionError(f"Could not connect: {exc}") from exc
        except Exception as exc:
            raise QbitConnectionError(f"Unexpected error: {exc}") from exc

    async def app_version(self) -> str:
        if self._client is None:
            raise QbitConnectionError("Not connected")
        return await asyncio.to_thread(lambda: str(self._client.app.version))

    # ── add + selective download flow ──
    async def add_torrent_selective(
        self,
        torrent_path: str,
        info_hash: str,
        all_indices: Iterable[int],
        selected_indices: Iterable[int],
        save_path: str | None = None,
        wait_seconds: float = 15.0,
        poll_interval: float = 0.5,
    ) -> str:
        """Add the torrent paused, set per-file priorities, then resume.

        Returns the torrent hash as registered by qBittorrent.

        Raises QbitAddError on failure.
        """
        if self._client is None:
            raise QbitConnectionError("Not connected")

        info_hash = info_hash.lower()
        all_idx = list(all_indices)
        sel_idx = [i for i in selected_indices if i in all_idx]
        skip_idx = [i for i in all_idx if i not in set(sel_idx)]

        # 1. add paused
        kwargs: dict = {
            "torrent_files": torrent_path,
            "is_paused": True,
        }
        if save_path:
            kwargs["save_path"] = save_path

        def _add() -> str:
            return self._client.torrents_add(**kwargs)

        try:
            result = await asyncio.to_thread(_add)
        except Exception as exc:
            raise QbitAddError(f"torrents_add failed: {exc}") from exc

        if isinstance(result, str) and result.strip().lower() != "ok.":
            raise QbitAddError(f"qBittorrent rejected the torrent: {result}")

        # 2. wait until torrent shows up by hash
        deadline = time.monotonic() + wait_seconds
        found = False
        while time.monotonic() < deadline:
            def _info():
                return self._client.torrents_info(torrent_hashes=info_hash)
            try:
                infos = await asyncio.to_thread(_info)
            except Exception:
                infos = []
            if infos:
                found = True
                break
            await asyncio.sleep(poll_interval)

        if not found:
            raise QbitAddError(
                f"Torrent {info_hash} did not register within {wait_seconds:.1f}s"
            )

        # 3. set ALL files to priority 0 (skip)
        if skip_idx:
            try:
                await asyncio.to_thread(
                    lambda: self._client.torrents_file_priority(
                        torrent_hash=info_hash,
                        file_ids=skip_idx,
                        priority=0,
                    )
                )
            except Exception as exc:
                raise QbitAddError(f"Failed to set skip priorities: {exc}") from exc

        # 4. set selected files to priority 1 (normal)
        if sel_idx:
            try:
                await asyncio.to_thread(
                    lambda: self._client.torrents_file_priority(
                        torrent_hash=info_hash,
                        file_ids=sel_idx,
                        priority=1,
                    )
                )
            except Exception as exc:
                raise QbitAddError(f"Failed to set download priorities: {exc}") from exc

        # 5. resume
        try:
            await asyncio.to_thread(
                lambda: self._client.torrents_resume(torrent_hashes=info_hash)
            )
        except Exception as exc:
            raise QbitAddError(f"Failed to resume torrent: {exc}") from exc

        return info_hash

    async def torrent_progress(self, info_hash: str) -> dict | None:
        """Snapshot of progress for a torrent. None if not found."""
        if self._client is None:
            return None
        info_hash = info_hash.lower()

        def _do() -> dict | None:
            infos = self._client.torrents_info(torrent_hashes=info_hash)
            if not infos:
                return None
            t = infos[0]
            return {
                "name": t.name,
                "state": t.state,
                "progress": float(t.progress),
                "dlspeed": int(t.dlspeed),
                "downloaded": int(t.downloaded),
                "size": int(t.size),
            }

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            return None
