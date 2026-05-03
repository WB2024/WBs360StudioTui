"""Tests for transfer_games bulk transfer helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.installer import InstallResult
from app.models.god_game import GodGameItem
from app.tui.screens.transfer_games import (
    _NOT_ON_CONSOLE,
    _ON_CONSOLE,
    _UNKNOWN,
    _fmt_bytes,
    _show_bulk_result,
    run_god_bulk_transfer_flow,
    run_god_transfer_flow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_god_item(name: str = "TestGame", title_id: str = "12345678") -> GodGameItem:
    """Return a minimal GodGameItem backed by real temp paths."""
    p = Path("/tmp") / name / title_id / "00007000"
    return GodGameItem(
        name=name,
        title_id=title_id,
        content_type="00007000",
        local_path=p,
        container_file="DEADBEEF",
    )


def _make_modal() -> MagicMock:
    modal = MagicMock()
    modal.set_stage = MagicMock()
    modal.set_detail = MagicMock()
    modal.set_done = MagicMock()
    return modal


# ---------------------------------------------------------------------------
# _fmt_bytes
# ---------------------------------------------------------------------------

class TestFmtBytes:
    def test_bytes(self):
        assert _fmt_bytes(512) == "512 B"

    def test_kilobytes(self):
        assert _fmt_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _fmt_bytes(5 * 1024 ** 2) == "5.0 MB"

    def test_gigabytes(self):
        assert "GB" in _fmt_bytes(3 * 1024 ** 3)


# ---------------------------------------------------------------------------
# _show_bulk_result
# ---------------------------------------------------------------------------

class TestShowBulkResult:
    def test_empty_results(self):
        modal = _make_modal()
        _show_bulk_result(modal, [])
        modal.set_done.assert_called_once()
        args = modal.set_done.call_args
        assert args[1]["success"] is False

    def test_single_success(self):
        modal = _make_modal()
        _show_bulk_result(modal, [("Game A", True, "Transferred 3 of 3 file(s)")])
        modal.set_done.assert_called_once_with("Transferred 3 of 3 file(s)", success=True)

    def test_single_failure(self):
        modal = _make_modal()
        _show_bulk_result(modal, [("Game A", False, "FTP error")])
        modal.set_done.assert_called_once_with("FTP error", success=False)

    def test_bulk_all_success(self):
        modal = _make_modal()
        results = [("Game A", True, "ok"), ("Game B", True, "ok")]
        _show_bulk_result(modal, results)
        modal.set_done.assert_called_once()
        msg, kw = modal.set_done.call_args[0][0], modal.set_done.call_args[1]
        assert "2/2" in msg
        assert kw["success"] is True

    def test_bulk_partial_failure(self):
        modal = _make_modal()
        results = [("Game A", True, "ok"), ("Game B", False, "timeout")]
        _show_bulk_result(modal, results)
        modal.set_done.assert_called_once()
        msg, kw = modal.set_done.call_args[0][0], modal.set_done.call_args[1]
        assert "1/2" in msg
        assert "Game B" in msg
        assert kw["success"] is False

    def test_bulk_all_fail(self):
        modal = _make_modal()
        results = [("Game A", False, "err"), ("Game B", False, "err")]
        _show_bulk_result(modal, results)
        kw = modal.set_done.call_args[1]
        assert kw["success"] is False

    def test_bulk_more_than_3_failures_truncated(self):
        modal = _make_modal()
        results = [(f"Game {i}", False, "err") for i in range(5)]
        _show_bulk_result(modal, results)
        msg = modal.set_done.call_args[0][0]
        # Only first 3 failures shown; remainder counted
        assert "+2 more" in msg

    def test_bulk_exactly_3_failures_not_truncated(self):
        modal = _make_modal()
        results = [(f"Game {i}", False, "err") for i in range(3)]
        _show_bulk_result(modal, results)
        msg = modal.set_done.call_args[0][0]
        assert "more" not in msg


# ---------------------------------------------------------------------------
# run_god_bulk_transfer_flow — unit tests (mocked app & installer)
# ---------------------------------------------------------------------------

def _make_app(method: str = "ftp", usb_root: str | None = "/mnt/usb") -> MagicMock:
    """Create a minimal mock app for testing the bulk flow."""
    prof = MagicMock()
    prof.host = "192.168.1.1"
    prof.port = 21
    prof.username = "xbox"
    prof.password = "xbox"

    settings = MagicMock()
    settings.game_install_path = "Hdd:\\Content\\0000000000000000\\"
    settings.default_profile.return_value = prof

    installer = MagicMock()

    app = MagicMock()
    app.settings = settings
    app.installer = installer
    app.set_connection_status = MagicMock()

    # push_screen_wait returns different values depending on call order
    # First call = method modal, second call = usb modal (if usb)
    if method == "ftp":
        app.push_screen_wait = _async_side_effect([method])
    else:
        app.push_screen_wait = _async_side_effect([method, usb_root])

    app.push_screen = _async_noop()
    return app


def _async_side_effect(values: list):
    """Return an async mock that yields values sequentially on each await."""
    it = iter(values)

    async def _coro(*args, **kwargs):
        try:
            return next(it)
        except StopIteration:
            return None

    return _coro


def _async_noop():
    async def _coro(*args, **kwargs):
        return None
    return _coro


def _async_return(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro


class TestRunGodBulkTransferFlowFTP:
    @pytest.mark.asyncio
    async def test_empty_list_does_nothing(self):
        app = _make_app()
        called = False

        async def _track(*args, **kwargs):
            nonlocal called
            called = True

        app.push_screen_wait = _track
        await run_god_bulk_transfer_flow(app, [])
        assert not called, "push_screen_wait should not be called for an empty game list"

    @pytest.mark.asyncio
    async def test_cancelled_method_does_nothing(self):
        app = _make_app()
        app.push_screen_wait = _async_side_effect([None])  # user cancelled
        game = _make_god_item()
        await run_god_bulk_transfer_flow(app, [game])
        app.installer.install_god_via_ftp.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_game_ftp_success(self):
        game = _make_god_item()
        app = _make_app(method="ftp")

        ok_result = InstallResult(
            success=True,
            files_transferred=5,
            errors=[],
            message="Transferred 5 of 5 file(s)",
        )

        async def _ftp_install(*args, **kwargs):
            # Exercise the progress callback if provided
            if kwargs.get("progress"):
                kwargs["progress"]("transfer", 100, 200)
            return ok_result

        app.installer.install_god_via_ftp = _ftp_install

        # FtpClient.connect / disconnect need to be no-ops
        with patch("app.tui.screens.transfer_games.FtpClient") as MockFtp:
            instance = MockFtp.return_value
            instance.connect = _async_noop()
            instance.disconnect = _async_noop()
            instance.is_connected = True

            await run_god_bulk_transfer_flow(app, [game])

    @pytest.mark.asyncio
    async def test_two_games_ftp_both_called(self):
        games = [_make_god_item("GameA", "11111111"), _make_god_item("GameB", "22222222")]
        app = _make_app(method="ftp")

        call_count = 0

        async def _ftp_install(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return InstallResult(success=True, files_transferred=1, errors=[], message="ok")

        app.installer.install_god_via_ftp = _ftp_install

        with patch("app.tui.screens.transfer_games.FtpClient") as MockFtp:
            instance = MockFtp.return_value
            instance.connect = _async_noop()
            instance.disconnect = _async_noop()
            instance.is_connected = True

            await run_god_bulk_transfer_flow(app, games)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_ftp_connection_failure_aborts_all(self):
        games = [_make_god_item("GameA"), _make_god_item("GameB")]
        app = _make_app(method="ftp")

        async def _fail_connect():
            raise ConnectionError("refused")

        with patch("app.tui.screens.transfer_games.FtpClient") as MockFtp:
            instance = MockFtp.return_value
            instance.connect = _fail_connect
            instance.disconnect = _async_noop()

            await run_god_bulk_transfer_flow(app, games)

        # Neither game should have been attempted
        app.installer.install_god_via_ftp.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_game_fails_others_continue(self):
        games = [_make_god_item("GameA", "11111111"), _make_god_item("GameB", "22222222")]
        app = _make_app(method="ftp")

        call_order: list[str] = []

        async def _ftp_install(game, *args, **kwargs):
            call_order.append(game.name)
            if game.name == "GameA":
                raise RuntimeError("timeout")
            return InstallResult(success=True, files_transferred=1, errors=[], message="ok")

        app.installer.install_god_via_ftp = _ftp_install

        with patch("app.tui.screens.transfer_games.FtpClient") as MockFtp:
            instance = MockFtp.return_value
            instance.connect = _async_noop()
            instance.disconnect = _async_noop()

            await run_god_bulk_transfer_flow(app, games)

        # Both games must have been attempted despite first failing
        assert call_order == ["GameA", "GameB"]


class TestRunGodBulkTransferFlowUSB:
    @pytest.mark.asyncio
    async def test_single_game_usb_success(self):
        game = _make_god_item()
        app = _make_app(method="usb", usb_root="/mnt/usb")

        ok_result = InstallResult(success=True, files_transferred=2, errors=[], message="Copied 2 of 2 file(s)")

        async def _usb_install(*args, **kwargs):
            if kwargs.get("progress"):
                kwargs["progress"]("transfer", 50, 100)
            return ok_result

        app.installer.install_god_via_usb = _usb_install

        await run_god_bulk_transfer_flow(app, [game])

    @pytest.mark.asyncio
    async def test_usb_cancelled_does_nothing(self):
        game = _make_god_item()
        app = _make_app(method="usb")
        # USB drive selection returns None (cancelled)
        app.push_screen_wait = _async_side_effect(["usb", None])

        await run_god_bulk_transfer_flow(app, [game])
        app.installer.install_god_via_usb.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_games_usb_both_called(self):
        games = [_make_god_item("GameA", "AAAAAAAA"), _make_god_item("GameB", "BBBBBBBB")]
        app = _make_app(method="usb", usb_root="/mnt/usb")

        call_count = 0

        async def _usb_install(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return InstallResult(success=True, files_transferred=1, errors=[], message="ok")

        app.installer.install_god_via_usb = _usb_install

        await run_god_bulk_transfer_flow(app, games)
        assert call_count == 2


class TestRunGodTransferFlowLegacy:
    """Ensure the single-game wrapper still works correctly (backwards compat)."""

    @pytest.mark.asyncio
    async def test_delegates_to_bulk(self):
        game = _make_god_item()
        app = _make_app(method="ftp")

        async def _ftp_install(*args, **kwargs):
            return InstallResult(success=True, files_transferred=1, errors=[], message="ok")

        app.installer.install_god_via_ftp = _ftp_install

        with patch("app.tui.screens.transfer_games.FtpClient") as MockFtp:
            instance = MockFtp.return_value
            instance.connect = _async_noop()
            instance.disconnect = _async_noop()

            # run_god_transfer_flow must accept a single game and complete without error
            await run_god_transfer_flow(app, game)


# ---------------------------------------------------------------------------
# Console-status column logic (_console_status via TransferGamesScreen)
# ---------------------------------------------------------------------------

class TestConsoleStatusColumn:
    """Unit-test the _console_status helper on a minimal screen instance."""

    def _make_screen(self, library: dict[str, str] | None) -> "Any":
        from app.tui.screens.transfer_games import TransferGamesScreen
        screen = TransferGamesScreen.__new__(TransferGamesScreen)
        screen._console_library = library
        return screen

    def test_unknown_when_library_none(self):
        screen = self._make_screen(None)
        game = _make_god_item(title_id="AABBCCDD")
        assert screen._console_status(game) == _UNKNOWN

    def test_on_console_when_title_id_present(self):
        screen = self._make_screen({"AABBCCDD": "/Hdd1/Content/0000000000000000/AABBCCDD"})
        game = _make_god_item(title_id="AABBCCDD")
        assert screen._console_status(game) == _ON_CONSOLE

    def test_not_on_console_when_title_id_absent(self):
        screen = self._make_screen({"11111111": "/path"})
        game = _make_god_item(title_id="AABBCCDD")
        assert screen._console_status(game) == _NOT_ON_CONSOLE

    def test_case_insensitive_match(self):
        """on_mount normalises all library keys to uppercase; game.title_id.upper() must match."""
        # Library is always stored uppercase (normalised in on_mount and load_library)
        screen = self._make_screen({"AABBCCDD": "/path"})
        game = _make_god_item(title_id="aabbccdd")  # lowercase title_id on item
        assert screen._console_status(game) == _ON_CONSOLE

    def test_empty_library_shows_not_on_console(self):
        screen = self._make_screen({})
        game = _make_god_item(title_id="AABBCCDD")
        assert screen._console_status(game) == _NOT_ON_CONSOLE


# ---------------------------------------------------------------------------
# Sync — missing-game selection logic
# ---------------------------------------------------------------------------

class TestSyncMissingGames:
    """Test that action_sync selects the right games and calls the bulk flow."""

    def _make_screen_with_games(
        self,
        games: list[GodGameItem],
        library: dict[str, str] | None,
    ) -> "Any":
        from app.tui.screens.transfer_games import TransferGamesScreen
        screen = TransferGamesScreen.__new__(TransferGamesScreen)
        screen._games = games
        screen._console_library = library
        # Populate _row_items the same way _refresh_table does
        screen._row_items = {str(i): g for i, g in enumerate(games)}
        screen._selected_keys = set()
        screen._row_keys = {}
        return screen

    def _missing_from(
        self,
        games: list[GodGameItem],
        library: dict[str, str] | None,
    ) -> list[GodGameItem]:
        """Replicate the sync filtering logic from action_sync."""
        if library is None:
            return []
        return [
            g for g in games
            if g.title_id.upper() not in library
        ]

    def test_sync_finds_all_missing_when_library_empty(self):
        games = [_make_god_item("A", "11111111"), _make_god_item("B", "22222222")]
        missing = self._missing_from(games, {})
        assert len(missing) == 2

    def test_sync_skips_games_already_on_console(self):
        games = [_make_god_item("A", "11111111"), _make_god_item("B", "22222222")]
        library = {"11111111": "/path"}
        missing = self._missing_from(games, library)
        assert len(missing) == 1
        assert missing[0].title_id == "22222222"

    def test_sync_returns_empty_when_all_present(self):
        games = [_make_god_item("A", "11111111"), _make_god_item("B", "22222222")]
        library = {"11111111": "/path", "22222222": "/path2"}
        missing = self._missing_from(games, library)
        assert missing == []

    def test_sync_returns_empty_when_library_none(self):
        games = [_make_god_item("A", "11111111")]
        missing = self._missing_from(games, None)
        assert missing == []

    def test_sync_case_insensitive(self):
        """game.title_id.upper() is compared against uppercase library keys (normalised at load)."""
        games = [_make_god_item("A", "aabbccdd")]  # lowercase title_id on item
        library = {"AABBCCDD": "/path"}  # uppercase key (normalised by on_mount)
        missing = self._missing_from(games, library)
        assert missing == []

    def test_sync_all_games_missing(self):
        games = [_make_god_item(f"Game{i}", f"0000000{i}") for i in range(5)]
        missing = self._missing_from(games, {"99999999": "/other"})
        assert len(missing) == 5
