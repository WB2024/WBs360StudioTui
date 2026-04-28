"""Async HTTP downloader with progress callbacks."""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import httpx

from app.core.constants import HTTP_TIMEOUT

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (bytes_so_far, total_bytes)


class DownloadError(Exception):
    pass


class Downloader:
    def __init__(self, timeout: float = HTTP_TIMEOUT) -> None:
        self.timeout = timeout

    async def download(
        self,
        url: str,
        destination: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        raise DownloadError(f"HTTP {resp.status_code} for {url}")
                    total = int(resp.headers.get("Content-Length") or 0)
                    received = 0
                    with tmp.open("wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            f.write(chunk)
                            received += len(chunk)
                            if progress_callback:
                                try:
                                    progress_callback(received, total)
                                except Exception:
                                    log.exception("progress_callback raised")
            tmp.replace(destination)
            return destination
        except DownloadError:
            tmp.unlink(missing_ok=True)
            raise
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"Network error downloading {url}: {e}") from e

    async def download_to_memory(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise DownloadError(f"HTTP {resp.status_code} for {url}")
            return resp.content

    async def fetch_text(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise DownloadError(f"HTTP {resp.status_code} for {url}")
            return resp.text
