# Audiobook TTS (Kokoro + UI)

Desktop UI to load a TXT file, split it into sections, select which sections to generate, and export an audiobook `.m4b` file.

## Input format

The parser detects section headers that start with one of these:

- `CHAPTER ...`
- `EXTRA ...`
- `SIDE STORY ...`

Example:

```text
CHAPTER 1: Arrival
This is the first chapter text.

SIDE STORY: Tavern Scene
Extra side content.

EXTRA: Author Notes
Notes text.
```

If no headers are found, the whole file is treated as one chapter.

## Requirements

- Python 3.10+
- `ffmpeg` on your system `PATH` (required for `.m4b` export)

On Windows, after installing ffmpeg, verify:

```powershell
ffmpeg -version
```

## Setup

From project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .
```

## Run

Optional: choose Kokoro voice:

```powershell
$env:ABTTS_VOICE = "af_heart"
```

Start app:

```powershell
abtts
```

Or:

```powershell
python -m abtts
```

## Use in UI

1. Choose or drag a `.txt` file.
2. Select sections to generate.
3. Optionally change output folder.
4. Click `Generate`.
5. Wait for completion.

## Output

- One `.m4b` audiobook file is created for the selected sections.
- Section `.wav` files are also generated.
- If `.m4b` packaging fails (for example ffmpeg missing), an error is shown and `.wav` files remain available.
