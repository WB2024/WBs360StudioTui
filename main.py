"""x360tm — Xbox 360 Mod Manager TUI entry point."""
import os
import sys
from pathlib import Path

from app.config.settings import load_settings
from app.tui.app import X360TuiApp

_WEB_SENTINEL = "X360TM_WEB_WORKER"


def _run_web_server(host: str, port: int) -> None:
    """Entry point for the web-server worker process."""
    from textual_serve.server import Server

    if getattr(sys, "frozen", False):
        cmd = str(Path(sys.argv[0]).resolve())
    else:
        cmd = f"{sys.executable} {Path(__file__).resolve()}"

    Server(cmd, host=host, port=port, title="x360tm").serve()


def main() -> None:
    # ── Web-server worker ────────────────────────────────────────────────────
    # When textual-serve spawns a subprocess for each browser tab it sets the
    # environment variable X360TM_NO_WEB so that instance doesn't re-launch
    # the web server recursively.
    if os.environ.get(_WEB_SENTINEL) == "server":
        # This process IS the web server — parse args and serve.
        import json
        args = json.loads(os.environ["X360TM_WEB_ARGS"])
        _run_web_server(**args)
        return

    settings = load_settings()

    if settings.web_server_enabled and not os.environ.get("X360TM_NO_WEB"):
        import json
        import multiprocessing

        os.environ["X360TM_NO_WEB"] = "1"

        args = {"host": settings.web_server_host, "port": settings.web_server_port}
        env = {**os.environ, _WEB_SENTINEL: "server", "X360TM_WEB_ARGS": json.dumps(args)}

        p = multiprocessing.Process(
            target=_run_web_server,
            args=(settings.web_server_host, settings.web_server_port),
            daemon=True,
        )
        p.start()

    app = X360TuiApp()
    result = app.run()
    if result == "restart":
        # Linux: binary was replaced in-place by the updater; re-exec to run the new version.
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
