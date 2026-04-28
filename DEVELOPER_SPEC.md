# Xbox 360 Mod Manager TUI — Developer Specification
### For Claude Opus 4.7 — Full End-to-End Implementation

---

## 1. Project Overview

**Project Name:** `x360tm` (Xbox 360 TUI Manager) — or similar, developer may choose.

Build a cross-platform **Terminal User Interface (TUI)** application that allows users to browse, download, and install mods, homebrew, trainers, and game saves for the **Xbox 360 only**. The application fetches its data from Arisen Studio's publicly hosted JSON databases at `https://db.arisen.studio` and transfers files to the console either via **FTP** or **USB**.

This application must run on **Windows** and **Linux** without modification.

---

## 2. Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | **Python 3.11+** | Cross-platform, rich TUI ecosystem, rapid development |
| TUI Framework | **Textual** (by Textualize) | Modern, async-first, mouse+keyboard, CSS-like styling |
| HTTP Client | **httpx** (async) | Async downloads, streaming support |
| FTP Client | **ftplib** (stdlib) + **aioftp** | Async FTP transfers |
| Archive Handling | **zipfile**, **tarfile** (stdlib) | Unzip mod archives in-memory or to temp dir |
| USB Detection | **psutil** + **os** (stdlib) | Cross-platform drive/mount detection |
| Config Storage | **platformdirs** + **json** | OS-appropriate config directory |
| Dependency Mgmt | **Poetry** or **pip + requirements.txt** | Developer's choice; requirements.txt preferred for simplicity |
| Packaging | **PyInstaller** | Single-binary builds for Windows (.exe) and Linux |

---

## 3. Data Sources

All data is fetched from Arisen Studio's public database. **Xbox 360 relevant endpoints only:**

```
Base URL: https://db.arisen.studio

/data/categories.json              — Category metadata (names, IDs, types)
/data/xbox360/game-mods.json       — Game Mods library
/data/xbox360/homebrew.json        — Homebrew library
/data/xbox360/trainers.json        — Trainers library
/data/xbox360/game-cheats.json     — Game Cheats library
/data/xbox360/game-patches.zip     — Game Patches (zipped collection)
/data/game-saves.json              — Game Saves (shared PS3+Xbox; filter by platform field)
/data/xbox360/titleids.json        — Title ID ↔ Game Name lookup table
```

### Data Fetch Strategy
- On **first launch**, fetch all JSON databases and cache them locally (see §8 Config & Cache).
- On **subsequent launches**, check `https://db.arisen.studio/app/status.txt` to determine if a refresh is needed, or simply re-fetch on every launch (data is small).
- Cache location: OS config dir (e.g. `~/.config/x360tm/cache/` on Linux, `%APPDATA%\x360tm\cache\` on Windows).
- Each JSON file is saved locally as-is. Game patches ZIP is downloaded on demand.

### JSON Structure Notes

**game-mods.json / homebrew.json** — Array of `ModItemData`:
```json
{
  "Id": 123,
  "CategoryId": "halo3",
  "Name": "Mod Name",
  "Version": "1.0",
  "Author": "AuthorName",
  "Description": "...",
  "ModType": "Multiplayer",
  "FirmwareTypes": ["Any"],
  "Region": "ALL",
  "DownloadFiles": [
    {
      "Name": "filename.zip",
      "Url": "https://...",
      "InstallPaths": ["Hdd1:\\Path\\To\\Install\\"]
    }
  ]
}
```

**trainers.json** — Array of `TrainerGameItem`:
```json
{
  "TitleId": "545107D4",
  "GameTitle": "Halo 3",
  "Trainers": [
    {
      "Name": "Trainer Name",
      "Type": "aurora",
      "Url": "https://...",
      "InstallPaths": ["Hdd1:\\Path\\"],
      "LastUpdated": "2024-01-01"
    }
  ]
}
```

**game-saves.json** — Array of `GameSaveItemData`:
```json
{
  "Id": 456,
  "Platform": "Xbox360",
  "CategoryId": "halo3",
  "Name": "100% Save",
  "Region": "ALL",
  "Version": "1.0",
  "DownloadFiles": [
    {
      "Name": "save.zip",
      "Url": "https://...",
      "InstallPaths": ["Hdd1:\\Content\\0000000000000000\\545107D4\\000B0000\\"]
    }
  ]
}
```

> **Game Saves filtering:** Filter `game-saves.json` entries where `"Platform": "Xbox360"` (case-insensitive). Discard PS3 entries entirely.

---

## 4. Application Structure

```
x360tm/
├── main.py                  # Entry point
├── requirements.txt
├── README.md
├── app/
│   ├── __init__.py
│   ├── tui/
│   │   ├── __init__.py
│   │   ├── app.py           # Main Textual App class
│   │   ├── screens/
│   │   │   ├── splash.py        # Startup/loading screen
│   │   │   ├── connection.py    # FTP connection setup screen
│   │   │   ├── main_menu.py     # Main navigation screen
│   │   │   ├── game_mods.py     # Game Mods browser screen
│   │   │   ├── homebrew.py      # Homebrew browser screen
│   │   │   ├── trainers.py      # Trainers browser screen
│   │   │   ├── game_saves.py    # Game Saves browser screen
│   │   │   ├── game_cheats.py   # Game Cheats browser screen
│   │   │   ├── game_patches.py  # Game Patches browser screen
│   │   │   ├── install.py       # Install progress/confirmation screen
│   │   │   └── settings.py      # Settings screen
│   │   ├── widgets/
│   │   │   ├── mod_table.py     # Reusable sortable/filterable mod list widget
│   │   │   ├── mod_detail.py    # Detail panel widget (right side)
│   │   │   ├── status_bar.py    # Bottom status bar widget
│   │   │   ├── connection_bar.py # Top connection status indicator
│   │   │   └── progress_modal.py # Transfer progress modal
│   │   └── styles/
│   │       └── app.tcss         # Textual CSS stylesheet
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py          # Database fetch, cache, and query logic
│   │   ├── ftp_client.py        # FTP connection, upload, directory management
│   │   ├── usb_manager.py       # USB drive detection and file copy
│   │   ├── installer.py         # Orchestrates download → extract → transfer
│   │   └── downloader.py        # HTTP download with progress callbacks
│   ├── models/
│   │   ├── __init__.py
│   │   ├── mod_item.py          # ModItemData dataclass
│   │   ├── game_save.py         # GameSaveItemData dataclass
│   │   ├── trainer.py           # TrainerGameItem / TrainerItem dataclasses
│   │   ├── game_patch.py        # GamePatchItemData dataclass
│   │   └── connection.py        # ConnectionProfile dataclass
│   └── config/
│       ├── __init__.py
│       └── settings.py          # Load/save user settings and connection profiles
└── tests/
    ├── test_database.py
    ├── test_ftp_client.py
    └── test_installer.py
```

---

## 5. Screen & Navigation Flow

```
Launch
  └─► Splash Screen (loading databases, checking connectivity)
        └─► Main Menu
              ├─► Game Mods
              │     └─► Mod Detail → Install Flow
              ├─► Homebrew
              │     └─► App Detail → Install Flow
              ├─► Trainers
              │     └─► Trainer Detail → Install Flow
              ├─► Game Saves
              │     └─► Save Detail → Install Flow
              ├─► Game Cheats
              │     └─► Cheat Detail → View/Copy
              ├─► Game Patches
              │     └─► Patch Detail → Install Flow
              ├─► Settings
              │     ├─► Manage Connection Profiles (FTP)
              │     ├─► USB Install Path
              │     └─► Cache Management
              └─► Quit
```

**Install Flow (shared across all categories):**
```
Select Mod → View Detail Panel
  → Press [I] to Install
    → Choose Install Method: [F] FTP  |  [U] USB
      → If FTP:
          → If no active connection: show Connection Modal
          → Confirm install paths
          → Download file → Extract if zip → Upload via FTP → Show success
      → If USB:
          → Detect connected USB drives
          → User selects drive
          → Confirm install paths
          → Download file → Extract if zip → Copy to USB path → Show success
```

---

## 6. Screen Specifications

### 6.1 Splash Screen
- Display app name/logo in ASCII art
- Show loading progress: "Fetching Game Mods...", "Fetching Homebrew...", etc.
- Check `https://db.arisen.studio/app/status.txt` for service availability
- If fetch fails, offer to load from local cache (if available) or show error
- Auto-advance to Main Menu when complete

### 6.2 Connection Setup (Modal or Screen)
Triggered on first FTP install attempt or from Settings.

**Fields:**
| Field | Type | Default |
|---|---|---|
| IP Address | Text input | Last used / saved |
| Port | Number input | `21` |
| Username | Text input | `xbox` |
| Password | Password input (masked) | *(blank)* |
| Profile Name | Text input | "My Xbox 360" |

**Actions:**
- `[Test Connection]` — Attempts FTP connect, shows success/fail inline
- `[Save Profile]` — Saves to config
- `[Connect]` — Connects and returns to the install flow

**Multiple profiles supported** — user can save and switch between profiles (e.g. multiple consoles).

### 6.3 Main Menu
- Sidebar or centered menu with category tiles
- Show total item counts per category (loaded from cache)
- Show active FTP connection status in top bar (IP address + green/red indicator)
- Keyboard shortcuts displayed

### 6.4 Category Browser Screens (Game Mods / Homebrew / Trainers / Game Saves / Game Cheats / Game Patches)

**Layout:** Two-pane split
- **Left pane (60%):** Scrollable, filterable table/list of items
- **Right pane (40%):** Detail view of selected item

**Left Pane — Table Columns:**

*Game Mods / Homebrew:*
| Column | Notes |
|---|---|
| Name | Mod/app name |
| Category | Game name (from categories.json) |
| Version | |
| Author | |
| Type | Mod type |
| Region | |

*Trainers:*
| Column | Notes |
|---|---|
| Game Title | |
| Title ID | |
| Trainer Name | |
| Type | Aurora / XBDM |
| Last Updated | |

*Game Saves:*
| Column | Notes |
|---|---|
| Name | Save name |
| Game | From categories |
| Region | |
| Version | |

*Game Cheats:*
| Column | Notes |
|---|---|
| Game | |
| Cheat Name | |
| Description | |

*Game Patches:*
| Column | Notes |
|---|---|
| Game | |
| Patch Name | |
| Version | |

**Filtering (always visible above table):**
- Search box: filters `Name` and `Category` fields in real-time (debounced 300ms)
- Category dropdown: filter by game/category
- For Game Mods: additional Region and Mod Type dropdowns

**Right Pane — Detail View:**
- Full name
- Category / Game
- Author / Creator
- Version(s)
- Description (scrollable)
- Region, Firmware type (if applicable)
- For Trainers: Type (Aurora/XBDM), Last Updated
- File list with sizes (if available)
- Install Paths (displayed for user reference)
- `[I] Install`  `[D] Download Only`  `[O] Open URL]`

**Keyboard Shortcuts (all browser screens):**
| Key | Action |
|---|---|
| `↑` / `↓` | Navigate list |
| `/` or `F` | Focus search filter |
| `I` | Install selected |
| `D` | Download only (to local Downloads folder) |
| `R` | Refresh database |
| `Esc` | Back to main menu |
| `Q` | Quit |
| `Tab` | Switch focus between panes |

### 6.5 Install Progress Modal
- Overlay modal (non-blocking where possible)
- Steps displayed with status icons:
  - `[ ]` Pending / `[~]` In Progress / `[✓]` Done / `[✗]` Failed
  - Step 1: Downloading file... (with % and speed)
  - Step 2: Extracting archive...
  - Step 3: Connecting to Xbox 360... (FTP only)
  - Step 4: Creating remote directories... (FTP only)
  - Step 5: Transferring files... (with file count progress e.g. 2/5)
  - Step 6: Complete!
- `[Cancel]` button available during download/transfer
- On completion: `[Install Another]` / `[Close]`

### 6.6 Settings Screen
- **Connection Profiles:** List of saved FTP profiles, add/edit/delete, set default
- **USB Settings:** Default USB drive path override (optional)
- **Cache:** Show cache age, `[Refresh Now]` button, `[Clear Cache]`
- **Download Directory:** Where "Download Only" saves files (default: `~/Downloads/x360tm/`)
- **Theme:** Light / Dark (Textual built-in)
- **On Startup:** Always refresh DB / Use cache if < N hours old

---

## 7. Core Logic

### 7.1 Database Manager (`core/database.py`)

```python
class DatabaseManager:
    async def fetch_all(self) -> None
        # Fetch all Xbox 360 JSON endpoints concurrently
        # Save to cache dir as {name}.json

    async def load_all(self) -> None
        # Load from cache into memory as dataclass lists

    def get_game_mods(self, category_id=None, name=None, mod_type=None, region=None) -> List[ModItemData]
    def get_homebrew(self, category_id=None, name=None) -> List[ModItemData]
    def get_trainers(self, title_id=None, name=None) -> List[TrainerGameItem]
    def get_game_saves(self, category_id=None, name=None, region=None) -> List[GameSaveItemData]
    def get_game_cheats(self, ...) -> List[...]
    def get_game_patches(self, ...) -> List[...]
    def get_categories(self) -> List[CategoryItem]
    def resolve_category_name(self, category_id: str) -> str
    def resolve_game_title(self, title_id: str) -> str
```

- All filtering done in-memory after load (no remote query per filter)
- Filtering is case-insensitive substring match
- `game-saves.json` filtered to `Platform == "Xbox360"` on load

### 7.2 FTP Client (`core/ftp_client.py`)

```python
class FtpClient:
    def __init__(self, host: str, port: int, username: str, password: str)

    def connect(self) -> bool
        # Returns True on success, raises FtpConnectionError on failure

    def disconnect(self) -> None

    def test_connection(self) -> bool

    def ensure_directory(self, remote_path: str) -> None
        # Create directory and all parents if they don't exist
        # Xbox 360 FTP paths use backslash: Hdd1:\Path\To\Dir
        # Convert backslash to forward slash for FTP protocol

    def upload_file(self, local_path: str, remote_path: str, progress_callback=None) -> None
        # Upload single file with optional progress callback
        # progress_callback(bytes_sent, total_bytes)

    def list_directory(self, remote_path: str) -> List[str]

    def file_exists(self, remote_path: str) -> bool

    @property
    def is_connected(self) -> bool
```

**Path Handling — Critical:**
- `InstallPaths` in JSON use Windows-style backslashes: `Hdd1:\Content\...`
- Xbox 360 FTP servers accept forward slashes: `Hdd1:/Content/...`
- Normalize all paths: replace `\` with `/` before FTP operations
- Paths ending in `\` or `/` indicate a **directory** — the filename is appended
- Paths ending in a filename (has extension) indicate the exact destination path

### 7.3 Installer (`core/installer.py`)

```python
class Installer:
    async def install_via_ftp(
        self,
        item: ModItemData | GameSaveItemData | TrainerItem,
        ftp_client: FtpClient,
        progress_callback: Callable
    ) -> InstallResult

    async def install_via_usb(
        self,
        item: ModItemData | GameSaveItemData | TrainerItem,
        usb_path: str,
        progress_callback: Callable
    ) -> InstallResult

    async def download_only(
        self,
        item,
        destination_dir: str,
        progress_callback: Callable
    ) -> str  # returns path to downloaded file
```

**Install Algorithm (FTP):**
```
For each DownloadFile in item.DownloadFiles:
  1. Download file URL to temp directory
  2. If file is .zip:
       Extract to temp subdirectory
       Collect all extracted files with their relative paths
  3. Else:
       Single file list
  4. For each file to transfer:
       Determine remote path:
         - If InstallPath ends with / or \ : remote = InstallPath + filename
         - Else: remote = InstallPath (exact path including filename)
       Call ftp_client.ensure_directory(parent_of_remote_path)
       Call ftp_client.upload_file(local_file, remote_path, progress_cb)
  5. Clean up temp directory
```

**Install Algorithm (USB):**
```
Same as FTP but instead of FTP upload:
  - Map Xbox paths to USB paths:
      "Hdd1:\" → "{usb_root}\"  (or user-configured mapping)
  - Use shutil.copy2() for file transfer
  - Use os.makedirs() for directory creation
```

### 7.4 USB Manager (`core/usb_manager.py`)

```python
class UsbManager:
    def detect_drives(self) -> List[UsbDrive]
        # Windows: check drive letters A-Z, filter removable drives via psutil
        # Linux: check /media/{user}/ and /mnt/ mount points

    def get_available_space(self, drive_path: str) -> int  # bytes

    def map_xbox_path_to_usb(self, xbox_path: str, usb_root: str) -> str
        # "Hdd1:\Content\..." → "{usb_root}/Content/..."
        # Strip "Hdd1:" prefix, convert separators
```

**USB Path Mapping:**
The Xbox 360 reads content from a USB drive's `Content` directory structure. The path mapping strips the `Hdd1:` (or `Usb:`) prefix from InstallPaths and places files relative to the USB root.

### 7.5 Downloader (`core/downloader.py`)

```python
class Downloader:
    async def download(
        self,
        url: str,
        destination: Path,
        progress_callback: Callable[[int, int], None] = None
    ) -> Path
        # Streams download with progress
        # Returns path to downloaded file

    async def download_to_memory(self, url: str) -> bytes
        # For small files
```

---

## 8. Configuration & Cache

### Config File Location
- **Linux:** `~/.config/x360tm/settings.json`
- **Windows:** `%APPDATA%\x360tm\settings.json`
- Use `platformdirs.user_config_dir("x360tm")` to resolve

### Cache Location
- **Linux:** `~/.cache/x360tm/`
- **Windows:** `%LOCALAPPDATA%\x360tm\cache\`
- Use `platformdirs.user_cache_dir("x360tm")` to resolve

### `settings.json` Schema
```json
{
  "version": 1,
  "theme": "dark",
  "download_dir": "~/Downloads/x360tm",
  "db_cache_max_age_hours": 24,
  "connections": [
    {
      "id": "uuid",
      "name": "My Xbox 360",
      "host": "192.168.1.x",
      "port": 21,
      "username": "xbox",
      "password": "xbox",
      "is_default": true
    }
  ],
  "usb": {
    "auto_detect": true,
    "manual_path": null
  },
  "last_db_fetch": "2025-01-01T00:00:00Z"
}
```

### Cache Files
```
cache/
├── game-mods.json
├── homebrew.json
├── trainers.json
├── game-cheats.json
├── game-patches/       ← extracted from game-patches.zip
├── game-saves.json
├── categories.json
└── titleids.json
```

---

## 9. UI / UX Design

### Layout (all browser screens)
```
┌─────────────────────────────────────────────────────────┐
│ x360tm  │  Game Mods  │  Homebrew  │  Trainers  │  ... │  ← Tab bar
├──────────────────────────────────┬──────────────────────┤
│ 🔍 Search: [_____________]       │                      │
│ Category: [All ▼] Region:[All ▼] │   MOD DETAIL         │
├──────────────────────────────────│                      │
│ Name           │ Game   │ Author  │   Name: ...          │
│────────────────┼────────┼─────── │   Game: ...          │
│ ► Mod One      │ Halo 3 │ User1   │   Author: ...        │
│   Mod Two      │ CoD    │ User2   │   Version: ...       │
│   Mod Three    │ Halo 3 │ User3   │   Description:       │
│   ...          │        │         │   ...                │
│                │        │         │                      │
│                │        │         │   Install Paths:     │
│                │        │         │   Hdd1:\...          │
│                │        │         │                      │
│                │        │         │  [I]nstall [D]ownload│
├──────────────────────────────────┴──────────────────────┤
│ 📡 192.168.1.100:21 ✓ Connected │ 1,247 mods │ [Q]uit  │  ← Status bar
└─────────────────────────────────────────────────────────┘
```

### Color Scheme (Dark theme default)
| Element | Color |
|---|---|
| Header/Tab bar | Deep blue `#1a1a2e` |
| Selected row | Highlight blue `#16213e` |
| Focused border | Bright cyan `#00d4ff` |
| Success indicators | Green `#00ff88` |
| Error/warning | Red `#ff4444` |
| Muted text | Gray `#888888` |
| Status bar | Dark gray `#111111` |

### Trainer Type Indicators
- `[A]` — Aurora trainer (requires Aurora Dashboard on Xbox)
- `[X]` — XBDM trainer (requires XBDM debug monitor)
- Display type clearly in both list and detail view with tooltip/note explaining what each requires

---

## 10. Error Handling

| Scenario | Handling |
|---|---|
| No internet on launch | Offer to load from cache; show warning banner |
| FTP connection refused | Show inline error with retry button; suggest checking IP/firewall |
| FTP timeout during transfer | Show error with option to retry from last point (re-upload file) |
| Download URL 404/error | Show error with URL; suggest the mod may have been removed |
| ZIP extraction failure | Show error; offer to open containing folder |
| USB drive not detected | Instruct user to check USB connection; manual path entry fallback |
| Disk full (USB/local) | Check available space before transfer; warn if insufficient |
| Invalid install path | Log warning; show path to user; allow manual override |
| Cache corruption | Silently re-fetch; log to file |

All errors should be displayed **in the UI** (never raw tracebacks visible to user). Log full tracebacks to `~/.local/share/x360tm/x360tm.log` (Linux) or `%APPDATA%\x360tm\x360tm.log` (Windows).

---

## 11. Game Cheats — Special Handling

Game Cheats from `game-cheats.json` may not follow the standard DownloadFiles/InstallPaths pattern — they are often text-based cheat codes rather than files to transfer. Implement as follows:

- Parse and display cheat codes/descriptions in the detail pane
- Provide `[Copy to Clipboard]` for individual cheats
- If a cheat has an associated file URL, offer standard install flow
- If it's text-only, display cleanly with no install option

---

## 12. Game Patches — Special Handling

`game-patches.zip` is a ZIP archive containing multiple patch files. On first access:

1. Download `https://db.arisen.studio/data/xbox360/game-patches.zip`
2. Extract to `cache/game-patches/`
3. Parse extracted files to build the patches index
4. Cache the extracted contents (re-use until DB refresh)

Install flow for patches is the same as mods — use `InstallPaths` from the patch data.

---

## 13. Startup Sequence

```
1. Show splash screen with ASCII logo
2. Load settings from config file (create defaults if not exists)
3. Concurrently:
   a. Ping https://db.arisen.studio/app/status.txt
   b. Check local cache age
4. If online AND (no cache OR cache older than max_age):
   → Fetch all JSON databases concurrently
   → Save to cache
   → Update last_db_fetch timestamp
5. Else if cache exists:
   → Load from cache
   → Show "Using cached data from {date}" notice
6. Else:
   → Show "Unable to fetch data and no cache available" error screen
7. Load all data into memory as typed dataclasses
8. Navigate to Main Menu
```

---

## 14. Installation

### Requirements File (`requirements.txt`)
```
textual>=0.47.0
httpx>=0.26.0
aioftp>=0.21.0
psutil>=5.9.0
platformdirs>=4.1.0
rich>=13.7.0
```

### Running
```bash
# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

### Building Standalone Binary
```bash
# Windows
pyinstaller --onefile --name x360tm main.py

# Linux
pyinstaller --onefile --name x360tm main.py
```

---

## 15. File: `main.py`

```python
from app.tui.app import X360TuiApp

if __name__ == "__main__":
    app = X360TuiApp()
    app.run()
```

---

## 16. Out of Scope

The following are explicitly **not** required:
- PS3 / PS4 support (Xbox 360 only)
- Online multiplayer / lobby features
- Mod creation or editing tools
- Any write-back to Arisen Studio's database
- User accounts or authentication with arisen.studio
- Auto-update of the application itself (only DB data updates)
- Plugin system
- GUI (graphical) mode

---

## 17. Testing Requirements

- Unit tests for `DatabaseManager` (filtering, parsing, platform filtering for game saves)
- Unit tests for `FtpClient` path normalization (backslash → forward slash)
- Unit tests for `Installer` path resolution logic (directory vs. exact file paths)
- Unit tests for `UsbManager` path mapping
- Mock HTTP responses using `respx` for downloader tests
- Mock FTP server using `pytest-asyncio` + `aioftp` test server for FTP tests

---

## 18. Deliverables Checklist

- [ ] Full working TUI application matching this spec
- [ ] All 6 content categories implemented (Game Mods, Homebrew, Trainers, Game Saves, Game Cheats, Game Patches)
- [ ] FTP install flow working end-to-end
- [ ] USB install flow working end-to-end
- [ ] Download-only flow working
- [ ] Connection profile management (save/load/switch)
- [ ] Real-time search/filter on all category screens
- [ ] Settings screen fully functional
- [ ] Error handling per §10
- [ ] `requirements.txt` present and complete
- [ ] PyInstaller build tested on Windows and Linux
- [ ] Unit tests for core logic modules
- [ ] `README.md` with install and usage instructions

---

## 19. Notes for Claude Opus 4.7

- **Start with `core/` modules first** — get database fetching, FTP, and installer logic working before building TUI screens.
- **Textual documentation:** https://textual.textualize.io — use `DataTable` widget for the mod lists, `TabbedContent` for category tabs, `ModalScreen` for the install progress and connection modals.
- **Path normalization is critical** — all `InstallPaths` from the JSON use Windows backslashes. Always normalize to forward slashes before any FTP call.
- **The `game-saves.json` is shared with PS3** — always filter by `"Platform": "Xbox360"` on load.
- **Trainer types matter** — always display whether a trainer requires Aurora or XBDM clearly; some users will only have one or the other.
- **Don't hardcode any URLs** — keep all database and status URLs in a single constants file (mirroring Arisen Studio's `Urls.cs` pattern) so they can be updated easily.
- **Use `async`/`await` throughout** — Textual is async-native; all I/O (HTTP, FTP, file) should be non-blocking using `asyncio` to keep the UI responsive during transfers.
- **Test the FTP path handling** with both directory-style paths (`Hdd1:\Content\`) and file-style paths (`Hdd1:\Content\file.bin`) — both patterns appear in the JSON data.