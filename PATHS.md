# x360tm — Path Reference

Everything the application reads from or writes to, and how each path is determined.

---

## 1. Application Data Directories (automatic, OS-managed)

These paths are determined at runtime by [platformdirs](https://github.com/platformdirs/platformdirs) using the app name `x360tm`. They are **never exposed in Settings** — the app creates them automatically.

| Purpose | Linux path | Windows path |
|---|---|---|
| **Config / settings** | `~/.config/x360tm/` | `%APPDATA%\x360tm\` |
| **Cache** (DB JSON files, library scan) | `~/.cache/x360tm/` | `%LOCALAPPDATA%\x360tm\Cache\` |
| **Logs** | `~/.local/state/x360tm/` | `%LOCALAPPDATA%\x360tm\log\` |
| **User data** (iso2god binary) | `~/.local/share/x360tm/` | `%LOCALAPPDATA%\x360tm\` |

### Files inside the config directory

| File | Contents |
|---|---|
| `settings.json` | All user settings (see §2). Written atomically via `.json.tmp` then rename. |

### Files inside the cache directory

| File | Written by | Used by |
|---|---|---|
| `categories.json` | DB fetch | All browser screens (category filtering) |
| `game-mods.json` | DB fetch | Game Mods screen |
| `homebrew.json` | DB fetch | Homebrew screen |
| `trainers.json` | DB fetch | Trainers screen |
| `game-cheats.json` | DB fetch | Game Cheats screen |
| `game-saves.json` | DB fetch | Game Saves screen |
| `titleids.json` | DB fetch | Title ID → game name lookup everywhere |
| `game-patches.zip` | DB fetch | Game Patches screen |
| `library.json` | Library scan (FTP) | Library screen; filters all browser screens to "owned" games |
| `x360tm-linux.tar.gz` / `x360tm-windows.zip` | Self-updater | Applied during update, then deleted |

### Files inside the user data directory

| File | Written by | Used by |
|---|---|---|
| `tools/iso2god` (Linux) | Auto-downloaded on first ISO→GOD conversion | ISO→GOD screen, Pipeline screen |
| `tools/iso2god.exe` (Windows) | Auto-downloaded on first ISO→GOD conversion | ISO→GOD screen, Pipeline screen |

---

## 2. User-configurable Paths (Settings screen)

All of these are stored in `settings.json` and editable via **Settings → Local Paths** or **Settings → Console Paths**.

### Local Paths

#### Local Downloads
- **Settings key:** `download_dir`
- **Default:** `~/Downloads/x360tm/`
- **Used by:** "Download Only" install option in any browser screen (Mods, Homebrew, Saves, Trainers, Patches). Files are saved here when you press `I` → Download.

#### Local ISO Path
- **Settings key:** `local_iso_path`
- **Default:** *(empty — feature disabled until set)*
- **Used by:** ISO→GOD conversion screen; Pipeline screen (step 1: scans this folder for `.iso` files). Supports two layouts:
  - Flat: `{path}/{GameName}.iso`
  - Subfoldered: `{path}/{GameName}/{GameName}.iso`

#### Local GOD Path
- **Settings key:** `local_god_path`
- **Default:** *(empty — feature disabled until set)*
- **Used by:**
  - **Transfer Games screen** — scans this folder for GOD-format games to send to the console or USB. Expects structure: `{path}/{GameName}/{TitleID}/{ContentType}/{ContainerFile}`.
  - **Pipeline screen** — ISO→GOD conversion outputs here; renamed games land here before transfer.

#### Torrent Download Folder
- **Settings key:** `torrent_download_folder`
- **Default:** *(empty — uses qBittorrent's own default save path)*
- **Used by:** Torrent selector screen — passed to qBittorrent as the `savepath` when sending a torrent. Also used as the scan root in the Pipeline screen to find archives/ISOs to process.

#### USB Backup Directory
- **Settings key:** `backup_dir`
- **Default:** `{repo root}/USBBackups/` *(falls back when field is empty)*
- **Used by:** USB Backup/Restore screen — `.pcl.zst` image files and `.meta.json` sidecar files are stored here.

### Console Paths

These are **Xbox-style paths** (e.g. `Hdd:\Aurora\`, `Usb1:\Games`) that refer to locations on the Xbox 360 itself, accessed over FTP.

#### Aurora Folder Path
- **Settings key:** `aurora_path`
- **Default:** `Hdd:\Aurora\`
- **Used by:** Trainer and mod installs. Install paths in the Arisen Studio database often contain the placeholder `{AURORAPATH}`, which is replaced with this value before the FTP upload. Example: `{AURORAPATH}\User\Trainers\4D5307E6\` → `Hdd:\Aurora\User\Trainers\4D5307E6\`.

#### Game Library Paths
- **Settings key:** `game_paths` (list, semicolon-separated in the UI)
- **Default:** *(empty — library scan skipped until set)*
- **Used by:** **Library screen** — scans these Xbox paths over FTP for subfolders whose names are exactly 8 hex characters (Title IDs). Results are saved to `library.json` in the cache dir and used to mark games as "owned" across all browser screens.
- **Example values:** `Usb1:\Games`, `Hdd:\Content\0000000000000000`, `Usb0:\Games;Usb1:\Games`

#### Library Scan Depth
- **Settings key:** `game_scan_depth`
- **Default:** `4`
- **Used by:** Library scan. Controls how many directory levels deep the scanner goes looking for 8-hex-char Title ID folders. Set to 4 if games are nested inside a friendly-named parent folder (e.g. `Games/Minecraft/4D530A81`).

#### Game Install Destination
- **Settings key:** `game_install_path`
- **Default:** `Hdd:\Content\0000000000000000\`
- **Used by:**
  - **Transfer Games screen** — FTP destination root for GOD game transfers.
  - **Pipeline screen** — final FTP transfer destination.
  - **Title Updates screen** — derives the target USB drive letter from this path's drive prefix (e.g. `Usb1:\...` → Title Updates go to the `Usb1` root on USB).

---

## 3. Fixed Repo-root Paths (hardcoded, not configurable)

These paths are resolved relative to the application's installation directory (the repo root when running from source, or the extracted binary folder when frozen). They are **not exposed in Settings**.

| Path | Module | Contents / Purpose |
|---|---|---|
| `LocalTrainers/{TitleID}/{file}.xex` | `local_library.py` | Locally-stored trainer files. Scanned at startup and merged into the Trainers browser as "local" source items. |
| `LocalMods/{TitleID}/{file}` | `local_library.py` | Locally-stored mod files. Merged into the Mods browser. |
| `LocalHomebrew/{AppName}/{file}` | `local_library.py` | Locally-stored homebrew apps. Merged into the Homebrew browser. |
| `LocalGameSaves/{TitleID}/{file}` | `local_library.py` | Locally-stored game saves. Merged into the Game Saves browser. |
| `LocalPatches/` | `local_library.py` | Locally-stored patch TOML files. Referenced in Game Patches browser. |
| `LocalCheats/` | `local_library.py` | Locally-stored cheat files. Referenced in Game Cheats browser. |
| `LocalTitleUpdates/{TitleID} - {Name}/{file}` | `tu_scanner.py` | Locally-stored STFS Title Update packages. Scanned on launch of the Title Updates screen. Any nesting depth is supported. |
| `Torrent/` | `torrent_picker.py` | Drop `.torrent` files here. The Torrent Picker screen lists all `.torrent` files in this folder. |
| `BadAvatarFiles/` | `bad_avatar.py` | Source files for BadAvatar USB creation (JRPC2.xex, Xbdm.xex, BadUpdatePayload/, Apps/, Content/). Must be present for the Bad Avatar feature to work. |
| `USBBackups/` | `usb_backup.py` | **Default** backup image destination (used when `backup_dir` setting is blank). |
| `gamelist_xbox360.csv` | `database.py` | Bundled Title ID → game name lookup table (tab-separated). Used to resolve game names when the Arisen Studio `titleids.json` doesn't have an entry. |

---

## 4. Install Destination Paths

These are the paths that content lands on when you press Install from any browser screen.

### 4a. Mods, Trainers, Game Saves, Homebrew (via FTP or USB)

The destination path for each file comes directly from the `InstallPaths` field in the Arisen Studio database record for that item. The installer reads this list and places files accordingly.

**`{AURORAPATH}` placeholder** — any path containing `{AURORAPATH}` has it replaced with the value from **Settings → Console Paths → Aurora Folder Path** (default `Hdd:\Aurora\`) before the file is uploaded.

**Path resolution rules** (`installer.py: _resolve_remote_paths`):

| `install_paths` count | Local files count | Result |
|---|---|---|
| 1 path | any | All local files placed inside that directory, preserving zip subdirectory structure |
| N paths | N files | 1-to-1: file[i] → directory[i] (used for multi-file installs with distinct destinations) |
| N paths | ≠ N files | Broadcast: every file copied to every install path (common for trainers that need to exist on both Hdd and each Usb drive) |

**Typical console destination paths (examples from the DB):**

| Content type | Typical Xbox FTP path |
|---|---|
| Trainer | `{AURORAPATH}\User\Trainers\{TitleID}\{TrainerName}.xex` |
| Mod (plugin) | `{AURORAPATH}\Plugins\{TitleID}\{filename}` |
| Game save | `Hdd:\Content\{ProfileID}\{TitleID}\000D0000\{savefile}` |
| Homebrew app | `Hdd:\Content\0000000000000000\{TitleID}\000D0000\{filename}` |

> **Note:** These paths come from the Arisen Studio community database, not from this application. The actual path for any given item depends on what the database author specified.

**Staging (applies to all FTP and USB installs):**

1. `tempfile.mkdtemp(prefix="x360tm-")` is created in the OS temp directory.
2. The file is downloaded (or copied from a `LocalMods/` / `LocalTrainers/` / etc. source path) into the temp dir.
3. If the downloaded file is a `.zip`, it is extracted into `{tempdir}/extracted/` and all files within are collected.
4. Files are transferred to their resolved destination (FTP or USB).
5. The temp dir is deleted regardless of success or failure.

**USB path mapping:** The Xbox-style destination path (e.g. `Hdd:\Aurora\User\Trainers\4D530EF2\trainer.xex`) is converted to a local USB path via `map_xbox_path_to_usb()`, which strips the drive prefix and appends the remaining path to the USB mount root (e.g. `{usb_mount}/Aurora/User/Trainers/4D530EF2/trainer.xex`).

**Download Only mode:** Files are saved to:
```
{download_dir}/{item_name_sanitised}/{original_filename}
```
where `download_dir` comes from **Settings → Local Paths → Local Downloads** (default `~/Downloads/x360tm/`).

---

### 4b. GOD Games (via FTP or USB)

GOD (Games on Demand) games are transferred from your **Local GOD Path** (`local_god_path` setting).

**FTP install path:**
```
{game_install_path}/{TitleID}/{ContentType}/{relative_file_path}
```
- `game_install_path` from **Settings → Console Paths → Game Install Destination** (default `Hdd:\Content\0000000000000000\`)
- `ContentType` is the subfolder from the GOD container structure (e.g. `00007000`)
- Example: `Hdd:\Content\0000000000000000\4D5307E6\00007000\4D5307E6`

**USB install path:**
```
{usb_mount}/{dest_xbox_path_without_drive}/{TitleID}/{ContentType}/{relative_file_path}
```
- Example: USB mount at `/media/will/XBOX`, game_install_path `Usb1:\Content\0000000000000000\` → `/media/will/XBOX/Content/0000000000000000/4D5307E6/00007000/4D5307E6`

---

### 4c. Title Updates (via FTP or USB)

Title Update (TU) STFS packages are always placed at the standard Xbox 360 content path regardless of any settings.

**FTP install path:**
```
/{game_drive}/Content/0000000000000000/{TitleID}/000B0000/{filename}
```
- `game_drive` is the drive letter derived from the **Game Install Destination** setting (e.g. `game_install_path = "Usb1:\..."` → `game_drive = "Usb1"`)
- Example: `/Usb1/Content/0000000000000000/4D5307E6/000B0000/TU000000001`

**USB install path:**
```
{usb_mount}/Content/0000000000000000/{TitleID}/000B0000/{filename}
```
- Example: `/media/will/XBOX/Content/0000000000000000/4D5307E6/000B0000/TU000000001`

The source file for a TU install is read from `LocalTitleUpdates/` (local TUs) or from a path the user selected in the Title Updates browser screen.

---

## 5. Temporary Paths (runtime only)

| Path | Created by | Purpose |
|---|---|---|
| `{OS temp dir}/x360tm-{random}/` | `installer.py` | Staging area for downloads before FTP/USB transfer. Extracted zip contents land here. Deleted after install completes or fails. |
| `{OS temp dir}/x360tm_update_{random}/` | `updater.py` (Linux) | Extracted update binary during self-update. Renamed atomically over the running executable. |

---

## 6. Xbox 360 Console Paths (FTP)

These are paths **on the console**, accessed via Aurora's FTP server. The FTP root maps drive letters as follows:

| Xbox drive | FTP root directory |
|---|---|
| `Hdd:` / `Hdd1:` | `/Hdd1/` |
| `Usb0:` | `/Usb0/` |
| `Usb1:` | `/Usb1/` |
| `Usb:` | `/Usb0/` |
| `DvdRom0:` | `/Game/` |

### Paths written to the console

See [§4](#4-install-destination-paths) for the exact path structures used during content installs.

| Xbox path pattern | Written by | Contents |
|---|---|---|
| `{AURORAPATH}\User\Trainers\{TitleID}\{file}` | Trainer installer (§4a) | Trainer `.xex` file(s) |
| `{AURORAPATH}\Plugins\{TitleID}\{file}` | Mod installer (§4a) | Mod plugin file(s) |
| `Hdd:\Content\{ProfileID}\{TitleID}\000D0000\{file}` | Game Save installer (§4a) | Save file(s) |
| `{game_install_path}\{TitleID}\{ContentType}\{file}` | GOD transfer (§4b) | GOD game containers |
| `/{game_drive}/Content/0000000000000000/{TitleID}/000B0000/{file}` | Title Update installer (§4c) | STFS TU package |

### Paths read from the console

| Xbox path | Read by | Contents |
|---|---|---|
| Paths from `game_paths` setting | Library scanner | Scanned for 8-hex-char Title ID subfolders |
| Any path navigated to in File Manager | FTP browser screen | Directory listing |

---

## 7. USB Drive Paths

When installing to USB the app maps Xbox-style paths to a physical USB mount point.

- **USB drive detection:** `psutil.disk_partitions()` — on Linux, only `/media/`, `/mnt/`, and `/run/media/` mount points are considered removable.
- **Manual override:** If `usb.manual_path` is set in Settings (`Settings → USB Mount (Manual)`), that path is used directly instead of auto-detection.
- **Path mapping:** Xbox path `Usb1:\Content\0000000000000000\{TitleID}` → `{usb_mount_point}/Content/0000000000000000/{TitleID}` (drive prefix stripped, backslashes converted).

### Title Update USB install path

Title Updates are placed at `{usb_root}/Content/0000000000000000/{TitleID}/000B0000/{filename}` — the drive letter is derived from the **Game Install Destination** setting so they land on the same drive as your games.

---

## 8. Bad Avatar USB

When using the Bad Avatar creator:

| Path | Type | Purpose |
|---|---|---|
| `BadAvatarFiles/` | Repo-root (read) | Source files copied to the USB |
| `BadAvatarFiles/BadUpdatePayload/` | Repo-root (read) | Required minimum — confirms source is valid |
| `/media/{USER}/BADUPDATE/` | USB mount (written) | Formatted FAT32 partition labelled `BADUPDATE`; source files are copied here |
| `Usb:\Apps\Aurora\Aurora.xex` | Xbox path in `launch.ini` | Aurora executable reference baked into the `launch.ini` written to the USB |
