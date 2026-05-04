"""x360tm — Xbox 360 Mod Manager TUI entry point."""
import os
import sys
import threading
from pathlib import Path

from app.config.settings import load_settings
from app.tui.app import X360TuiApp


def _web_server_thread(host: str, port: int) -> None:
    """Run textual-serve in a daemon thread (one instance per browser connection)."""
    import asyncio
    from textual_serve.server import Server

    if getattr(sys, "frozen", False):
        # PyInstaller binary — run itself
        cmd = str(Path(sys.argv[0]).resolve())
    else:
        # Source / venv — run this script via the same interpreter
        cmd = f"{sys.executable} {Path(__file__).resolve()}"

    server = Server(cmd, host=host, port=port, title="x360tm")
    asyncio.run(server.serve())


def main() -> None:
    settings = load_settings()

    if settings.web_server_enabled and not os.environ.get("X360TM_NO_WEB"):
        # Mark env so subprocesses spawned by textual-serve don't recurse
        os.environ["X360TM_NO_WEB"] = "1"
        t = threading.Thread(
            target=_web_server_thread,
            args=(settings.web_server_host, settings.web_server_port),
            daemon=True,
        )
        t.start()

    app = X360TuiApp()
    result = app.run()
    if result == "restart":
        # Linux: binary was replaced in-place by the updater; re-exec to run the new version.
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
