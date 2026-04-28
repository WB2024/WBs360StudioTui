# x360tm — Xbox 360 Mod Manager TUI

Cross-platform terminal app for browsing and installing Xbox 360 mods, homebrew, trainers, game saves, cheats, and patches from the [Arisen Studio](https://arisen.studio) public databases.

## Features

- Browse 6 categories: Game Mods, Homebrew, Trainers, Game Saves, Game Cheats, Game Patches
- Real-time search/filter
- Install via FTP (to console) or USB (to drive)
- Download-only mode
- Connection profile management
- Async fetching, caching, and transfers

## Install

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Build standalone

```bash
pip install pyinstaller
pyinstaller --onefile --name x360tm main.py
```

## Keyboard

| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate |
| `/` | Focus search |
| `I` | Install |
| `D` | Download (via install dialog) |
| `R` | Refresh table |
| `Esc` | Back |
| `Q` | Quit |

## Config + Cache locations

- Settings: `platformdirs.user_config_dir("x360tm")/settings.json`
- Cache: `platformdirs.user_cache_dir("x360tm")/`
- Logs: `platformdirs.user_log_dir("x360tm")/x360tm.log`

## Notes

- Xbox 360 only — PS3/PS4 entries are filtered out.
- FTP install paths are normalized to forward slashes; directory paths get the filename appended.
- USB install strips the `Hdd1:`/`Usb:` prefix and writes relative to the chosen drive root.
