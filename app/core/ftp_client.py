"""Async FTP client for Xbox 360 transfers."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import aioftp

from app.core.constants import XBOX_DRIVE_TO_FTP
from app.core.paths import normalize_xbox_path, parent_dir, split_xbox_drive


def _ftp_path(path: str) -> str:
    """Map an Xbox install path to an FTP absolute path.

    Aurora FTP root lists drives as: Hdd1, Usb0, Usb1, Game, etc.
    e.g. 'Hdd:\\Aurora\\...' → '/Hdd1/Aurora/...'
         'Usb0:\\Aurora\\...' → '/Usb0/Aurora/...'
    """
    norm = normalize_xbox_path(path)
    prefix_lc = next((p for p in XBOX_DRIVE_TO_FTP if norm.lower().startswith(p)), None)
    if prefix_lc:
        ftp_drive = XBOX_DRIVE_TO_FTP[prefix_lc]
        rest = norm[len(prefix_lc):].lstrip("/")
        return f"/{ftp_drive}/{rest}" if rest else f"/{ftp_drive}"
    # No recognised prefix — assume already absolute or relative
    return norm if norm.startswith("/") else "/" + norm

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

    # Timeout (seconds) for each FTP socket operation and for mkdir/upload calls.
    SOCKET_TIMEOUT = 20
    OP_TIMEOUT = 30

    async def connect(self) -> bool:
        try:
            self._client = aioftp.Client(
                socket_timeout=self.SOCKET_TIMEOUT,
                connection_timeout=10,
            )
            await self._client.connect(self.host, self.port)
            await self._client.login(self.username, self.password)
            return True
        except (OSError, aioftp.AIOFTPException, asyncio.TimeoutError) as e:
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

    async def _mkd(self, path: str) -> None:
        """Send a raw MKD command, ignoring errors (dir already exists, unsupported, etc.)."""
        try:
            await asyncio.wait_for(
                self._client.command(f"MKD {path}", expected_codes=("2xx", "5xx")),
                timeout=self.OP_TIMEOUT,
            )
        except Exception:
            pass  # Already exists or not supported — safe to ignore

    async def make_directory(self, ftp_path: str) -> None:
        """Create a directory on the FTP server using its absolute FTP path.

        No-op if the directory already exists.
        """
        if not self._client:
            raise FtpConnectionError("Not connected")
        await self._mkd(ftp_path)

    async def ensure_directory(self, remote_path: str) -> None:
        if not self._client:
            raise FtpConnectionError("Not connected")
        # Strip drive prefix — FTP server sees / as root, no Hdd:/Usb: prefixes
        norm = _ftp_path(remote_path).rstrip("/")
        if not norm or norm == "/":
            return
        # Walk and create each path segment using raw MKD (Xbox FTP doesn't support MLST/MLSD)
        parts = [p for p in norm.split("/") if p]
        current = ""
        for part in parts:
            current = current + "/" + part
            await self._mkd(current)

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

        ftp_remote = _ftp_path(remote_path)
        await self.ensure_directory(parent_dir(ftp_remote))

        total = local_path.stat().st_size
        sent = 0
        try:
            async with self._client.upload_stream(ftp_remote) as stream:
                with local_path.open("rb") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        await asyncio.wait_for(stream.write(chunk), timeout=self.OP_TIMEOUT)
                        sent += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(sent, total)
                            except Exception:
                                pass
        except asyncio.TimeoutError as e:
            raise FtpTransferError(f"Upload timed out for {ftp_remote}") from e
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Upload failed for {ftp_remote}: {e}") from e

    async def file_exists(self, remote_path: str) -> bool:
        if not self._client:
            raise FtpConnectionError("Not connected")
        try:
            return await self._client.exists(_ftp_path(remote_path))
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

    async def list_detail(self, remote_path: str) -> list[tuple[str, bool, int, str]]:
        """List directory contents with metadata.

        Returns a list of (name, is_dir, size_bytes, modified_str) tuples.
        modified_str is in YYYYMMDDHHMMSS format when available, else "".

        Aurora's FtpDll ignores path arguments to LIST and doesn't support MLSD.
        We CWD to the target path first, then LIST with no argument and raw_command="LIST".
        """
        if not self._client:
            raise FtpConnectionError("Not connected")
        results: list[tuple[str, bool, int, str]] = []
        try:
            async with asyncio.timeout(self.SOCKET_TIMEOUT):
                await self._client.change_directory(remote_path)
                async for ftp_path, info in self._client.list(raw_command="LIST"):
                    name = ftp_path.name
                    if not name or name in (".", ".."):
                        continue
                    is_dir = info.get("type", "file").lower() in ("dir", "cdir", "pdir")
                    try:
                        size = int(info.get("size", 0) or 0)
                    except (ValueError, TypeError):
                        size = 0
                    modified = str(info.get("modify", "") or "")
                    results.append((name, is_dir, size, modified))
        except asyncio.TimeoutError as e:
            raise FtpTransferError(f"Directory listing timed out for {remote_path}") from e
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Directory listing failed for {remote_path}: {e}") from e
        return results

    async def delete_file(self, remote_path: str) -> None:
        """Delete a file on the remote server (DELE)."""
        if not self._client:
            raise FtpConnectionError("Not connected")
        try:
            await asyncio.wait_for(
                self._client.command(f"DELE {remote_path}", expected_codes=("2xx",)),
                timeout=self.OP_TIMEOUT,
            )
        except asyncio.TimeoutError as e:
            raise FtpTransferError(f"Delete timed out: {remote_path}") from e
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Delete failed for {remote_path}: {e}") from e

    async def remove_directory(self, remote_path: str) -> None:
        """Remove a directory on the remote server (RMD). Must be empty."""
        if not self._client:
            raise FtpConnectionError("Not connected")
        try:
            await asyncio.wait_for(
                self._client.command(f"RMD {remote_path}", expected_codes=("2xx",)),
                timeout=self.OP_TIMEOUT,
            )
        except asyncio.TimeoutError as e:
            raise FtpTransferError(f"Remove directory timed out: {remote_path}") from e
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Remove directory failed for {remote_path}: {e}") from e

    async def rename(self, old_path: str, new_path: str) -> None:
        """Rename/move a file or directory (RNFR + RNTO)."""
        if not self._client:
            raise FtpConnectionError("Not connected")
        try:
            await asyncio.wait_for(
                self._client.command(f"RNFR {old_path}", expected_codes=("3xx",)),
                timeout=self.OP_TIMEOUT,
            )
            await asyncio.wait_for(
                self._client.command(f"RNTO {new_path}", expected_codes=("2xx",)),
                timeout=self.OP_TIMEOUT,
            )
        except asyncio.TimeoutError as e:
            raise FtpTransferError("Rename timed out") from e
        except aioftp.AIOFTPException as e:
            raise FtpTransferError(f"Rename failed: {e}") from e
