# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# ── Data files ────────────────────────────────────────────────────────────────
datas = []

# Textual bundles its own widget CSS; without this the TUI renders broken.
datas += collect_data_files("textual")

# Package metadata needed by aioftp, textual, and textual-serve at import time.
datas += copy_metadata("aioftp")
datas += copy_metadata("textual")
datas += copy_metadata("textual-serve")

# App's own stylesheet.  Placed at app/tui/styles/ inside _MEIPASS so that
# CSS_PATH = "styles/app.tcss" resolves correctly relative to app.py.
datas += [("app/tui/styles/app.tcss", "app/tui/styles")]

# Bundled game title lookup table.  All _CSV_PATH chains (4 levels up from
# app/tui/screens/*.py or 3 levels up from app/core/*.py) resolve to _MEIPASS
# root, so placing the file there makes them all work unchanged.
datas += [("gamelist_xbox360.csv", ".")]

# BadAvatarFiles — only bundled when present locally (gitignored; copyright-
# restricted). CI builds omit this; local builds include it automatically.
if os.path.isdir("BadAvatarFiles"):
    datas += [("BadAvatarFiles", "BadAvatarFiles")]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "aioftp",
        "textual.widgets",
        "textual.app",
        "textual_serve",
        "textual_serve.server",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="x360tm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon="Icons/Icon256.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
