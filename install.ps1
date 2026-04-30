# install.ps1 — installs x360tm for the current user (no admin required)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Binary    = Join-Path $ScriptDir 'x360tm.exe'

if (-not (Test-Path $Binary)) {
    Write-Error "x360tm.exe not found in $ScriptDir`nDownload the Windows release from:`n  https://github.com/WB2024/WBs360StudioTui/releases/latest"
    exit 1
}

# Paths (all user-scoped, no UAC needed)
$InstallDir  = Join-Path $env:LOCALAPPDATA 'Programs\x360tm'
$IconDir     = Join-Path $InstallDir 'Icons'
$StartMenu   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Administration'

# Create directories
foreach ($dir in @($InstallDir, $IconDir, $StartMenu)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
}

# Copy binary
Copy-Item -Force $Binary (Join-Path $InstallDir 'x360tm.exe')
Write-Host "Installed binary  ->  $InstallDir\x360tm.exe"

# Copy icon
$IconSrc = Join-Path $ScriptDir 'Icons\Icon256.ico'
if (Test-Path $IconSrc) {
    Copy-Item -Force $IconSrc (Join-Path $IconDir 'Icon256.ico')
    Write-Host "Installed icon    ->  $IconDir\Icon256.ico"
}

# Create Start Menu shortcut
$ShortcutPath = Join-Path $StartMenu 'Xbox360 Mod Manager TUI.lnk'
$Shell    = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath       = Join-Path $InstallDir 'x360tm.exe'
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation     = Join-Path $IconDir 'Icon256.ico'
$Shortcut.Description      = 'Xbox 360 game manager and transfer tool'
$Shortcut.Save()
Write-Host "Installed launcher ->  $ShortcutPath"

# Add InstallDir to user PATH if not already present
$UserPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($UserPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable('PATH', "$UserPath;$InstallDir", 'User')
    Write-Host "Added to user PATH ->  $InstallDir"
    Write-Host "NOTE: Open a new terminal for PATH changes to take effect."
}

Write-Host ""
Write-Host "Done! 'Xbox360 Mod Manager TUI' is now in Start Menu > Administration."
