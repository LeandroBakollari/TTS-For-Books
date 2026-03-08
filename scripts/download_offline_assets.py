from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

from huggingface_hub import hf_hub_download


REPO_ID = "hexgrad/Kokoro-82M"
FFMPEG_ESSENTIALS_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
VOICE_OPTIONS = [
    "af_heart",
    "af_bella",
    "af_sarah",
    "af_nicole",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bm_george",
]


def ensure_kokoro_assets(project_root: Path, voices: list[str]) -> None:
    kokoro_dir = project_root / "assets" / "kokoro"
    voices_dir = kokoro_dir / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)

    config_src = Path(hf_hub_download(repo_id=REPO_ID, filename="config.json"))
    model_src = Path(hf_hub_download(repo_id=REPO_ID, filename="kokoro-v1_0.pth"))
    shutil.copy2(config_src, kokoro_dir / "config.json")
    shutil.copy2(model_src, kokoro_dir / "kokoro-v1_0.pth")

    for voice in voices:
        voice_src = Path(hf_hub_download(repo_id=REPO_ID, filename=f"voices/{voice}.pt"))
        shutil.copy2(voice_src, voices_dir / f"{voice}.pt")


def ensure_ffmpeg_assets(project_root: Path) -> None:
    ffmpeg_dir = project_root / "assets" / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "ffmpeg.zip"
        urlretrieve(FFMPEG_ESSENTIALS_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            extracted = Path(td) / "extract"
            zf.extractall(extracted)

        ffmpeg_candidates = list(extracted.rglob("ffmpeg.exe"))
        ffprobe_candidates = list(extracted.rglob("ffprobe.exe"))
        if not ffmpeg_candidates or not ffprobe_candidates:
            raise RuntimeError("Could not find ffmpeg.exe/ffprobe.exe in downloaded archive.")

        shutil.copy2(ffmpeg_candidates[0], ffmpeg_dir / "ffmpeg.exe")
        shutil.copy2(ffprobe_candidates[0], ffmpeg_dir / "ffprobe.exe")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download offline assets for release builds.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Path to repository root.",
    )
    parser.add_argument(
        "--voices",
        nargs="*",
        default=VOICE_OPTIONS,
        help="Kokoro voice names to bundle.",
    )
    parser.add_argument("--skip-ffmpeg", action="store_true")
    parser.add_argument("--skip-kokoro", action="store_true")
    args = parser.parse_args()

    root = args.project_root.resolve()
    if not args.skip_kokoro:
        ensure_kokoro_assets(root, args.voices)
    if not args.skip_ffmpeg:
        ensure_ffmpeg_assets(root)

    print("Offline assets are ready under:")
    print(root / "assets")


if __name__ == "__main__":
    main()
