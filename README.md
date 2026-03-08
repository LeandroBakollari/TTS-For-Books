# Audiobook TTS (Kokoro + UI)

Desktop UI to load TXT/EPUB, select chapters, generate chapter WAV files, and export one `.m4b`.

## Important: this repo does not include large assets

To keep GitHub repo size under limits, these files are not committed:

- Kokoro model + voice files
- FFmpeg binaries

The app needs those assets to work.

## Recommended for users (Releases)

Use one of these from GitHub Releases:

1. `AudiobookTTS-Setup-<version>.exe`  
Install and run. No manual Python or FFmpeg install required.

2. `AudiobookTTS-Source-With-Downloader-<version>.zip`  
Contains source code + downloader scripts (no large model files inside zip).

## If running from source

Requirements:

- Python 3.11 recommended
- Windows 10/11

Setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .
```

Download required assets (Kokoro + FFmpeg):

```powershell
python scripts\download_offline_assets.py
```

or:

```powershell
scripts\download_assets.bat
```

Run:

```powershell
python -m abtts
```

## Build offline Windows app

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1
```

Output app folder:

- `dist\AudiobookTTS\`

## Build installer (Setup.exe)

Install Inno Setup, then run:

```powershell
iscc installers\abtts.iss /DMyAppVersion=0.1.0 /DMyAppName=AudiobookTTS
```

Output installer:

- `dist\installer\AudiobookTTS-Setup-0.1.0.exe`

## Build source zip for Releases (without large assets)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_release_source_zip.ps1 -Version 0.1.0
```

Output:

- `dist\release\AudiobookTTS-Source-With-Downloader-0.1.0.zip`

## Notes

- If assets are missing, app errors will tell you to run `scripts/download_offline_assets.py` or use Releases.
- WAV chapter files are still kept even if `.m4b` export fails.
