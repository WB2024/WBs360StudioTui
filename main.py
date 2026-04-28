"""x360tm — Xbox 360 Mod Manager TUI entry point."""
from app.tui.app import X360TuiApp


def main() -> None:
    app = X360TuiApp()
    app.run()


if __name__ == "__main__":
    main()
