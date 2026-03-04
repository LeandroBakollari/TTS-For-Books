from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from abtts.section_parser import Section
from abtts.tts.kokoro_engine import KokoroConfig, KokoroEngine


@dataclass(frozen=True)
class JobPlan:
    input_path: str
    output_dir: str
    sections: List[Section]
    selected_indices: List[int]


class JobWorker(QObject):
    progress = Signal(int, int, float, float)  # processed_chars, total_chars, cps, eta_seconds
    section_done = Signal(str)  # section display name
    finished = Signal(str)  # output folder path
    failed = Signal(str)  # error message

    def __init__(self, plan: JobPlan):
        super().__init__()
        self.plan = plan
        self._cancel = False

    @Slot()
    def run(self) -> None:
        """Generate audio with Kokoro, then export a single M4B audiobook."""
        temp_wav_path: Path | None = None
        try:
            out_dir = Path(self.plan.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            selected = [(i, self.plan.sections[i]) for i in self.plan.selected_indices]
            total_chars = sum(len(s.text.strip()) for _, s in selected) or 1
            processed = 0
            start = time.time()

            voice = os.getenv("ABTTS_VOICE", "af_heart").strip() or "af_heart"
            engine = KokoroEngine(KokoroConfig(voice=voice))

            book_base = self._book_base_name(self.plan.input_path)
            temp_wav_path = self._unique_path(out_dir / f"{book_base}.tmp.wav")
            m4b_path = self._unique_path(out_dir / f"{book_base}.m4b")

            with wave.open(str(temp_wav_path), "wb") as combined_wav:
                combined_wav.setnchannels(1)
                combined_wav.setsampwidth(2)
                combined_wav.setframerate(engine.cfg.sample_rate)

                wrote_any_audio = False

                for section_index, section in selected:
                    if self._cancel:
                        self.failed.emit("Cancelled.")
                        return

                    text = section.text.strip()
                    if not text:
                        self.section_done.emit(f"{self._display_name(section)} (skipped empty section)")
                        continue

                    audio_parts: List[np.ndarray] = []
                    for text_chunk in self._chunk_text(text):
                        if self._cancel:
                            self.failed.emit("Cancelled.")
                            return

                        chunk_audio = engine.synthesize_one(text_chunk)
                        if chunk_audio.size > 0:
                            audio_parts.append(chunk_audio)

                        processed += len(text_chunk)
                        elapsed = max(time.time() - start, 1e-6)
                        cps = processed / elapsed
                        remaining = max(total_chars - processed, 0)
                        eta = remaining / cps if cps > 0 else 0.0
                        self.progress.emit(min(processed, total_chars), total_chars, cps, eta)

                    if audio_parts:
                        output_name = self._section_filename(section_index, section)
                        output_path = out_dir / output_name
                        merged = np.concatenate(audio_parts)
                        self._write_wav(output_path, merged, engine.cfg.sample_rate)
                        combined_wav.writeframes(self._float_to_pcm16_bytes(merged))
                        wrote_any_audio = True
                        self.section_done.emit(f"{self._display_name(section)} -> {output_name}")
                    else:
                        self.section_done.emit(f"{self._display_name(section)} (no audio returned)")

            if not wrote_any_audio:
                self.failed.emit("No audio was generated from selected sections.")
                return

            self.section_done.emit("Packaging audiobook (.m4b)...")
            self._encode_m4b(temp_wav_path, m4b_path)
            self.section_done.emit(f"M4B ready -> {m4b_path.name}")

            elapsed = max(time.time() - start, 1e-6)
            final_cps = total_chars / elapsed
            self.progress.emit(total_chars, total_chars, final_cps, 0.0)
            self.finished.emit(str(out_dir))

        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if temp_wav_path and temp_wav_path.exists():
                try:
                    temp_wav_path.unlink()
                except OSError:
                    pass

    def cancel(self) -> None:
        self._cancel = True

    @staticmethod
    def _display_name(section: Section) -> str:
        return f"{section.kind.title()} - {section.title}"

    @staticmethod
    def _book_base_name(input_path: str) -> str:
        stem = Path(input_path).stem
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_")
        return safe or "audiobook"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for i in range(1, 10000):
            candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate unique output path for {path.name}")

    @staticmethod
    def _section_filename(index: int, section: Section) -> str:
        kind = section.kind.lower().replace(" ", "_")
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", section.title).strip("_")
        if not safe_title:
            safe_title = "untitled"
        return f"{index + 1:03d}_{kind}_{safe_title}.wav"

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 700) -> List[str]:
        words = text.split()
        if not words:
            return []

        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for w in words:
            add_len = len(w) if current_len == 0 else len(w) + 1
            if current_len + add_len > max_chars and current:
                chunks.append(" ".join(current))
                current = [w]
                current_len = len(w)
            else:
                current.append(w)
                current_len += add_len

        if current:
            chunks.append(" ".join(current))

        return chunks

    @staticmethod
    def _encode_m4b(input_wav: Path, output_m4b: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is not installed or not on PATH. Install ffmpeg to export .m4b. "
                "Section WAV files were still generated."
            )

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(input_wav),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
            str(output_m4b),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "Unknown ffmpeg error").strip()
            if len(err) > 700:
                err = err[-700:]
            raise RuntimeError(f"ffmpeg failed while creating M4B: {err}")

    @staticmethod
    def _float_to_pcm16_bytes(audio: np.ndarray) -> bytes:
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        return pcm.tobytes()

    @classmethod
    def _write_wav(cls, path: Path, audio: np.ndarray, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(cls._float_to_pcm16_bytes(audio))
