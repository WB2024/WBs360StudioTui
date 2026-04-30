"""x360tm — Xbox 360 Mod Manager TUI entry point."""
import os
import sys

from app.tui.app import X360TuiApp


def main() -> None:
    app = X360TuiApp()
    result = app.run()
    if result == "restart":
        # Linux: binary was replaced in-place by the updater; re-exec to run the new version.
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
