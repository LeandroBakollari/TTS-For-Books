from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

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
    progress = Signal(int, int, float, float, int, int)  # chars + timing + chunk progress
    now_doing = Signal(str)  # current action line
    section_done = Signal(str)  # section display name
    finished = Signal(str)  # output folder path
    failed = Signal(str)  # error message

    def __init__(self, plan: JobPlan):
        super().__init__()
        self.plan = plan
        self._cancel = False

    @Slot()
    def run(self) -> None:
        """
        Generate audio with Kokoro, export per-chapter WAVs, and also export a single M4B audiobook.

        Key behavior:
        - The M4B contains ONE chapter marker per selected CHAPTER section.
        - Chunking into parts happens only inside the chapter audio (spoken as Part 1/2/3 if enabled),
          but parts are NOT exposed as M4B chapters.
        """
        temp_wav_path: Path | None = None
        ffmeta_path: Path | None = None

        try:
            out_dir = Path(self.plan.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            selected = [(i, self.plan.sections[i]) for i in self.plan.selected_indices]
            total_chars = sum(len(s.text.strip()) for _, s in selected) or 1
            processed = 0
            completed_chunks = 0
            start = time.time()

            voice = os.getenv("ABTTS_VOICE", "af_heart").strip() or "af_heart"
            speak_part_headers = self._env_bool("ABTTS_SPEAK_PART_HEADERS", default=False)
            embed_m4b_chapters = self._env_bool("ABTTS_M4B_CHAPTERS", default=True)
            part_silence_s = self._env_float("ABTTS_PART_SILENCE", default=0.35, min_value=0.0, max_value=10.0)
            chapter_silence_s = self._env_float("ABTTS_CHAPTER_SILENCE", default=0.8, min_value=0.0, max_value=30.0)
            aac_bitrate = os.getenv("ABTTS_AAC_BITRATE", "96k").strip() or "96k"

            engine = KokoroEngine(KokoroConfig(voice=voice))

            book_base = self._book_base_name(self.plan.input_path)
            temp_wav_path = self._unique_path(out_dir / f"{book_base}.tmp.wav")
            m4b_path = self._unique_path(out_dir / f"{book_base}.m4b")

            # Chunking drives "Part N" only (not chapters)
            chunks_by_section: list[tuple[int, Section, list[str]]] = []
            for section_index, section in selected:
                section_text = section.text.strip()
                section_chunks = self._chunk_text(section_text) if section_text else []
                chunks_by_section.append((section_index, section, section_chunks))
            total_chunks = sum(len(chunks) for _, _, chunks in chunks_by_section)

            # Chapter markers: EXACTLY one per section
            chapter_marks: List[Tuple[int, int, str]] = []  # (start_sample, end_sample, title)

            part_silence = self._silence(engine.cfg.sample_rate, part_silence_s)
            chapter_silence = self._silence(engine.cfg.sample_rate, chapter_silence_s)

            with wave.open(str(temp_wav_path), "wb") as combined_wav:
                combined_wav.setnchannels(1)
                combined_wav.setsampwidth(2)
                combined_wav.setframerate(engine.cfg.sample_rate)

                wrote_any_audio = False
                total_written_samples = 0

                self.now_doing.emit("Preparing synthesis...")
                self.progress.emit(0, total_chars, 0.0, 0.0, 0, total_chunks)

                for section_index, section, section_chunks in chunks_by_section:
                    if self._cancel:
                        self.failed.emit("Cancelled.")
                        return

                    text = section.text.strip()
                    if not text:
                        self.section_done.emit(f"{self._display_name(section)} (skipped empty section)")
                        continue

                    # Start sample for this chapter marker (in the combined WAV timeline)
                    chapter_start = total_written_samples

                    audio_parts: List[np.ndarray] = []
                    section_chunk_total = len(section_chunks)

                    # IMPORTANT: We do NOT create chapter marks per part.
                    # We only create one mark after the whole chapter is synthesized.

                    for part_i, text_chunk in enumerate(section_chunks, start=1):
                        if self._cancel:
                            self.failed.emit("Cancelled.")
                            return

                        self.now_doing.emit(
                            f"Synthesizing {self._display_name(section)} "
                            f"(part {part_i}/{section_chunk_total})"
                        )

                        if speak_part_headers:
                            # Short pause, then "Chapter X - Title. Part N.", then pause, then content.
                            if part_silence.size > 0:
                                audio_parts.append(part_silence.copy())
                            header = self._spoken_part_header(section_index, section, part_i)
                            header_audio = engine.synthesize_one(header)
                            if header_audio.size > 0:
                                audio_parts.append(header_audio)
                            if part_silence.size > 0:
                                audio_parts.append(part_silence.copy())

                        chunk_audio = engine.synthesize_one(text_chunk)
                        if chunk_audio.size > 0:
                            audio_parts.append(chunk_audio)

                        # Pause after each part
                        if part_silence.size > 0:
                            audio_parts.append(part_silence.copy())

                        processed += len(text_chunk)
                        completed_chunks += 1
                        elapsed = max(time.time() - start, 1e-6)
                        cps = processed / elapsed
                        remaining = max(total_chars - processed, 0)
                        eta = remaining / cps if cps > 0 else 0.0
                        self.progress.emit(
                            min(processed, total_chars),
                            total_chars,
                            cps,
                            eta,
                            completed_chunks,
                            total_chunks,
                        )

                    if audio_parts:
                        merged = np.concatenate(audio_parts)

                        # Write per-chapter WAV (nice to keep)
                        output_name = self._section_filename(section_index, section)
                        output_path = out_dir / output_name
                        self._write_wav(output_path, merged, engine.cfg.sample_rate)

                        # Append to combined WAV
                        combined_wav.writeframes(self._float_to_pcm16_bytes(merged))
                        wrote_any_audio = True

                        section_samples = int(merged.shape[0])
                        total_written_samples += section_samples

                        # One chapter marker for the WHOLE chapter
                        chapter_end = chapter_start + section_samples
                        chapter_title = self._chapter_title(section_index, section)
                        chapter_marks.append((chapter_start, chapter_end, chapter_title))

                        # Add a pause between chapters (not inside the chapter marker)
                        if chapter_silence.size > 0:
                            combined_wav.writeframes(self._float_to_pcm16_bytes(chapter_silence))
                            total_written_samples += int(chapter_silence.shape[0])

                        self.section_done.emit(f"{self._display_name(section)} -> {output_name}")
                    else:
                        self.section_done.emit(f"{self._display_name(section)} (no audio returned)")

            if not wrote_any_audio:
                self.failed.emit("No audio was generated from selected chapters.")
                return

            # Chapter metadata (optional but recommended)
            if embed_m4b_chapters and chapter_marks:
                self.now_doing.emit("Writing M4B chapter metadata...")
                ffmeta_path = out_dir / f"{book_base}.chapters.ffmeta"
                self._write_ffmpeg_chapter_metadata(
                    ffmeta_path=ffmeta_path,
                    chapter_marks=chapter_marks,
                    sample_rate=engine.cfg.sample_rate,
                )
            else:
                ffmeta_path = None

            self.now_doing.emit("Encoding M4B...")
            self._encode_m4b(
                input_wav=temp_wav_path,
                output_m4b=m4b_path,
                aac_bitrate=aac_bitrate,
                ffmeta=ffmeta_path,
            )
            self.section_done.emit(f"M4B ready -> {m4b_path.name}")

            elapsed = max(time.time() - start, 1e-6)
            final_cps = total_chars / elapsed
            self.progress.emit(total_chars, total_chars, final_cps, 0.0, total_chunks, total_chunks)
            self.now_doing.emit("Done.")
            self.finished.emit(str(out_dir))

        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            for p in (temp_wav_path, ffmeta_path):
                if p and p.exists():
                    try:
                        p.unlink()
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
        # Keep simple: one WAV per chapter section.
        kind = section.kind.lower().replace(" ", "_")
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", section.title).strip("_") or "untitled"
        return f"{safe_title}.wav"

    def _chapter_title(self, index: int, section: Section) -> str:
        # This is what shows up in the iPhone Books chapter list.
        # NO parts here.
        return section.title

    def _spoken_part_header(self, index: int, section: Section, part_number: int) -> str:
        # This is only spoken audio, not metadata chapters.
        return f"Chapter {index + 1}. {section.title}. Part {part_number}."

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
    def _silence(sample_rate: int, seconds: float) -> np.ndarray:
        if seconds <= 0:
            return np.zeros((0,), dtype=np.float32)
        n = int(round(sample_rate * seconds))
        if n <= 0:
            return np.zeros((0,), dtype=np.float32)
        return np.zeros((n,), dtype=np.float32)

    @staticmethod
    def _write_ffmpeg_chapter_metadata(
        ffmeta_path: Path,
        chapter_marks: List[Tuple[int, int, str]],
        sample_rate: int,
    ) -> None:
        def samp_to_ms(samp: int) -> int:
            return int(round((samp / max(sample_rate, 1)) * 1000.0))

        lines: List[str] = []
        lines.append(";FFMETADATA1")

        for start_samp, end_samp, title in chapter_marks:
            start_ms = max(0, samp_to_ms(start_samp))
            end_ms = max(start_ms + 1, samp_to_ms(end_samp))
            safe_title = title.replace("\n", " ").strip()

            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={start_ms}")
            lines.append(f"END={end_ms}")
            lines.append(f"title={safe_title}")

        ffmeta_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _encode_m4b(
        input_wav: Path,
        output_m4b: Path,
        aac_bitrate: str,
        ffmeta: Optional[Path],
    ) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is not installed or not on PATH. Install ffmpeg to export .m4b. "
                "Chapter WAV files were still generated."
            )

        cmd: List[str] = [ffmpeg, "-y", "-i", str(input_wav)]

        if ffmeta is not None:
            cmd += ["-i", str(ffmeta), "-map_metadata", "1"]

        cmd += [
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            aac_bitrate,
            "-movflags",
            "+faststart",
            str(output_m4b),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "Unknown ffmpeg error").strip()
            if len(err) > 900:
                err = err[-900:]
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

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        v = raw.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
        return default

    @staticmethod
    def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            v = float(raw.strip())
        except ValueError:
            return default
        return max(min(v, max_value), min_value)