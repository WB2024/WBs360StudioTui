#!/usr/bin/env bash
# install.sh — installs x360tm for the current user (no sudo required)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="$SCRIPT_DIR/x360tm"

if [[ ! -f "$BINARY" ]]; then
    echo "Error: x360tm binary not found in $SCRIPT_DIR"
    echo "Download the Linux release archive from:"
    echo "  https://github.com/WB2024/WBs360StudioTui/releases/latest"
    exit 1
fi

INSTALL_DIR="$HOME/.local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
DESKTOP_DIR="$HOME/.local/share/applications"

mkdir -p "$INSTALL_DIR" "$ICON_DIR" "$DESKTOP_DIR"

# --- Binary ---
cp "$BINARY" "$INSTALL_DIR/x360tm"
chmod +x "$INSTALL_DIR/x360tm"
echo "Installed binary  →  $INSTALL_DIR/x360tm"

# --- Icon ---
if [[ -f "$SCRIPT_DIR/Icons/Icon256.ico" ]]; then
    cp "$SCRIPT_DIR/Icons/Icon256.ico" "$ICON_DIR/x360tm.ico"
    echo "Installed icon    →  $ICON_DIR/x360tm.ico"
fi

# --- Desktop entry ---
cat > "$DESKTOP_DIR/x360tm.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Xbox360 Mod Manager TUI
Comment=Xbox 360 game manager and transfer tool
Exec=$INSTALL_DIR/x360tm
Icon=$ICON_DIR/x360tm.ico
Terminal=true
Categories=System;Administration;
StartupNotify=false
EOF
echo "Installed launcher →  $DESKTOP_DIR/x360tm.desktop"

# --- Refresh caches ---
command -v update-desktop-database &>/dev/null \
    && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache &>/dev/null \
    && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo ""
echo "Done! 'Xbox360 Mod Manager TUI' is now in Applications > Administration."
echo ""
# Warn if ~/.local/bin is not in PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo "NOTE: $INSTALL_DIR is not in your PATH."
    echo "Add this line to your ~/.bashrc or ~/.profile, then open a new terminal:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
