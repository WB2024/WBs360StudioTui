"""Async FTP client for Xbox 360 transfers."""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import aioftp

from app.core.paths import normalize_xbox_path, parent_dir

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


class FtpConnectionError(Exception):
    pass


class FtpTransferError(Exception):
    pass


class FtpClient:
    def __init__(self, host: str, port: int = 21, username: str = "xbox", password: str = "xbox") -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self._client: Optional[aioftp.Client] = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> bool:
        try:
            self._client = aioftp.Client()
            await self._client.connect(self.host, self.port)
            await self._client.login(self.username, self.password)
            return True
        except (OSError, aioftp.AIOFTPException) as e:
            self._client = None
            raise FtpConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}") from e

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.quit()
            except Exception:
                pass
            finally:
                self._client = None

    async def __aenter__(self) -> "FtpClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()

    async def test_connection(self) -> bool:
        try:
            await self.connect()
            await self.disconnect()
            return True
        except FtpConnectionError:
            return False

    async def ensure_directory(self, remote_path: str) -> None:
        if not self._client:
            raise FtpConnectionError("Not connected")
        norm = normalize_xbox_path(remote_path).rstrip("/")
        if not norm or norm in ("/", ""):
            return
        # Walk parents
        parts = norm.split("/")
        current = ""
        for part in parts:
            if not part:
                current = "/"
                continue
            current = (current.rstrip("/") + "/" + part) if current else part
            try:
                await self._client.make_directory(current)
            except aioftp.AIOFTPException:
                # Likely already exists — ignore
                pass

    async def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        if not self._client:
            raise FtpConnectionError("Not connected")
        local_path = Path(local_path)
        if not local_path.exists():
            raise FtpTransferError(f"Local file not found: {local_path}")

        norm_remote = normalize_xbox_path(remote_path)
        await self.ensure_directory(parent_dir(norm_remote))

        total = local_path.stat().st_size
        sent = 0
        try:
            async with self._client.upload_stream(norm_remote) as stream:
                with local_path.open("rb") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        await stream.write(chunk)
                        sent += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(sent, total)
                            except Exception:
                                log.exception("progress_callback raised")
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Upload failed for {norm_remote}: {e}") from e

    async def file_exists(self, remote_path: str) -> bool:
        if not self._client:
            raise FtpConnectionError("Not connected")
        try:
            return await self._client.exists(normalize_xbox_path(remote_path))
        except aioftp.AIOFTPException:
            return False

    async def list_directory(self, remote_path: str) -> list[str]:
        if not self._client:
            raise FtpConnectionError("Not connected")
        norm = normalize_xbox_path(remote_path)
        names: list[str] = []
        try:
            async for path, _info in self._client.list(norm):
                names.append(str(path))
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"List failed for {norm}: {e}") from e
        return names
