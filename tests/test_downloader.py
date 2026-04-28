from pathlib import Path

import httpx
import pytest
import respx

from app.core.downloader import DownloadError, Downloader


@pytest.mark.asyncio
@respx.mock
async def test_download_writes_file(tmp_path: Path):
    url = "https://example.test/file.bin"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"hello world"))
    d = Downloader()
    out = tmp_path / "out.bin"
    await d.download(url, out)
    assert out.read_bytes() == b"hello world"


@pytest.mark.asyncio
@respx.mock
async def test_download_404(tmp_path: Path):
    url = "https://example.test/missing"
    respx.get(url).mock(return_value=httpx.Response(404))
    d = Downloader()
    with pytest.raises(DownloadError):
        await d.download(url, tmp_path / "x")


@pytest.mark.asyncio
@respx.mock
async def test_progress_callback(tmp_path: Path):
    url = "https://example.test/big"
    respx.get(url).mock(return_value=httpx.Response(
        200, content=b"x" * 1024, headers={"Content-Length": "1024"}
    ))
    received: list[tuple[int, int]] = []
    d = Downloader()
    await d.download(url, tmp_path / "f.bin", progress_callback=lambda c, t: received.append((c, t)))
    assert received
    assert received[-1][0] == 1024
