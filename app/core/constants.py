"""Centralized URL + app constants. No URL strings live elsewhere."""
from __future__ import annotations

APP_NAME = "x360tm"
APP_TITLE = "Xbox 360 Mod Manager"

# --- Arisen Studio public DB endpoints (Xbox 360 only) ---
BASE_URL = "https://db.arisen.studio"

STATUS_CHECK = f"{BASE_URL}/app/status.txt"
CATEGORIES_DATA = f"{BASE_URL}/data/categories.json"
GAME_MODS_XBOX = f"{BASE_URL}/data/xbox360/game-mods.json"
HOMEBREW_XBOX = f"{BASE_URL}/data/xbox360/homebrew.json"
TRAINERS_XBOX = f"{BASE_URL}/data/xbox360/trainers.json"
GAME_CHEATS_XBOX = f"{BASE_URL}/data/xbox360/game-cheats.json"
GAME_PATCHES_XBOX = f"{BASE_URL}/data/xbox360/game-patches.zip"
GAME_SAVES = f"{BASE_URL}/data/game-saves.json"
TITLE_IDS_XBOX = f"{BASE_URL}/data/xbox360/titleids.json"

# Default HTTP timeout (seconds)
HTTP_TIMEOUT = 30.0

# Xbox platform string in game-saves.json
XBOX_PLATFORM = "XBOX"  # arisen-studio uses "XBOX" / "PS3" / "PS4"
XBOX_PLATFORM_ALIASES = {"XBOX", "XBOX360", "X360"}

# Common drive prefixes used in InstallPaths
XBOX_DRIVE_PREFIXES = ("Hdd1:", "Hdd:", "Usb0:", "Usb1:", "Usb:", "DvdRom0:")

# Maps Xbox drive prefix (lowercase) → FTP root directory name (as exposed by Aurora FTP)
# Aurora FTP lists: Hdd1, Usb0, Usb1, System, HddX, SysExt, Game at the root.
XBOX_DRIVE_TO_FTP: dict[str, str] = {
    "hdd1:": "Hdd1",
    "hdd:": "Hdd1",
    "usb0:": "Usb0",
    "usb1:": "Usb1",
    "usb:": "Usb0",
    "dvdrom0:": "Game",
}
