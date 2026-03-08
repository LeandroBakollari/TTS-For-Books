param(
    [string]$PythonExe = "python",
    [string]$AppName = "AudiobookTTS",
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "assets\kokoro\config.json")) {
    throw "Missing Kokoro assets. Run: python scripts\download_offline_assets.py"
}
if (-not (Test-Path "assets\ffmpeg\ffmpeg.exe")) {
    throw "Missing ffmpeg assets. Run: python scripts\download_offline_assets.py"
}

& $PythonExe -m pip install -U pip
& $PythonExe -m pip install -e .
& $PythonExe -m pip install pyinstaller

$entry = "src\abtts\__main__.py"
$pyiArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $AppName,
    "--collect-all", "PySide6",
    "--collect-all", "kokoro",
    "--add-data", "assets;assets",
    $entry
)

& $PythonExe -m PyInstaller @pyiArgs

Write-Host ""
Write-Host "Build complete:"
Write-Host "  dist\$AppName\$AppName.exe"
Write-Host ""
Write-Host "If Inno Setup is installed, run:"
Write-Host "  iscc installers\abtts.iss /DMyAppVersion=$Version /DMyAppName=$AppName"
