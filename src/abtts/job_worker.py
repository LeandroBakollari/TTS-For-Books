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
from abtts.runtime_paths import bundled_path
from abtts.tts.kokoro_engine import KokoroConfig, KokoroEngine


@dataclass(frozen=True)
class GenerationSettings:
    voice: str = "af_heart"
    part_silence_s: float = 0.35
    chapter_silence_s: float = 0.8
    aac_bitrate: str = "96k"
    chunk_size: int = 700
    speak_part_headers: bool = False
    embed_m4b_chapters: bool = True


@dataclass(frozen=True)
class JobPlan:
    input_path: str
    output_dir: str
    sections: List[Section]
    selected_indices: List[int]
    settings: GenerationSettings


class JobWorker(QObject):
    progress = Signal(int, int, float, float, int, int)
    now_doing = Signal(str)
    section_done = Signal(str)
    section_status = Signal(int, str, str)  # index, status, detail
    finished = Signal(str)
    failed = Signal(str)
    paused = Signal(bool)
    log = Signal(str)

    def __init__(self, plan: JobPlan):
        super().__init__()
        self.plan = plan
        self._cancel = False
        self._pause_requested = False
        self._is_paused = False

    @Slot()
    def run(self) -> None:
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

            cfg = self.plan.settings
            self.log.emit(
                "Settings | "
                f"voice={cfg.voice}, part_silence={cfg.part_silence_s:.2f}s, "
                f"chapter_silence={cfg.chapter_silence_s:.2f}s, bitrate={cfg.aac_bitrate}, "
                f"chunk_size={cfg.chunk_size}, speak_part_headers={cfg.speak_part_headers}, "
                f"embed_m4b_chapters={cfg.embed_m4b_chapters}"
            )

            engine = KokoroEngine(KokoroConfig(voice=cfg.voice))
            self.log.emit(f"Loaded Kokoro engine with voice '{cfg.voice}'.")

            book_base = self._book_base_name(self.plan.input_path)
            temp_wav_path = self._unique_path(out_dir / f"{book_base}.tmp.wav")
            m4b_path = self._unique_path(out_dir / f"{book_base}.m4b")

            chunks_by_section: list[tuple[int, Section, list[str]]] = []
            for section_index, section in selected:
                section_text = section.text.strip()
                section_chunks = self._chunk_text(section_text, max_chars=cfg.chunk_size) if section_text else []
                chunks_by_section.append((section_index, section, section_chunks))
                self.log.emit(
                    f"Prepared {self._display_name(section)} | "
                    f"chars={len(section_text)} | chunks={len(section_chunks)}"
                )
            total_chunks = sum(len(chunks) for _, _, chunks in chunks_by_section)

            chapter_marks: List[Tuple[int, int, str]] = []
            part_silence = self._silence(engine.cfg.sample_rate, cfg.part_silence_s)
            chapter_silence = self._silence(engine.cfg.sample_rate, cfg.chapter_silence_s)

            with wave.open(str(temp_wav_path), "wb") as combined_wav:
                combined_wav.setnchannels(1)
                combined_wav.setsampwidth(2)
                combined_wav.setframerate(engine.cfg.sample_rate)

                wrote_any_audio = False
                total_written_samples = 0

                self.now_doing.emit("Preparing synthesis...")
                self.progress.emit(0, total_chars, 0.0, 0.0, 0, total_chunks)

                for section_index, section, section_chunks in chunks_by_section:
                    self._check_control_flags()
                    if self._cancel:
                        self.failed.emit("Cancelled.")
                        return

                    text = section.text.strip()
                    if not text:
                        self.section_status.emit(section_index, "Failed", "Empty section")
                        self.section_done.emit(f"{self._display_name(section)} (skipped empty section)")
                        self.log.emit(f"Skipped empty section: {self._display_name(section)}")
                        continue

                    self.section_status.emit(section_index, "Working", "Synthesizing")
                    chapter_start = total_written_samples
                    audio_parts: List[np.ndarray] = []
                    section_chunk_total = len(section_chunks)

                    for part_i, text_chunk in enumerate(section_chunks, start=1):
                        self._check_control_flags()
                        if self._cancel:
                            self.failed.emit("Cancelled.")
                            return

                        self.now_doing.emit(
                            f"Synthesizing {self._display_name(section)} "
                            f"(part {part_i}/{section_chunk_total})"
                        )

                        if cfg.speak_part_headers:
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
                        output_name = self._section_filename(section_index, section)
                        output_path = out_dir / output_name
                        self._write_wav(output_path, merged, engine.cfg.sample_rate)

                        combined_wav.writeframes(self._float_to_pcm16_bytes(merged))
                        wrote_any_audio = True

                        section_samples = int(merged.shape[0])
                        total_written_samples += section_samples

                        chapter_end = chapter_start + section_samples
                        chapter_title = self._chapter_title(section_index, section)
                        chapter_marks.append((chapter_start, chapter_end, chapter_title))

                        if chapter_silence.size > 0:
                            combined_wav.writeframes(self._float_to_pcm16_bytes(chapter_silence))
                            total_written_samples += int(chapter_silence.shape[0])

                        self.section_done.emit(f"{self._display_name(section)} -> {output_name}")
                        self.section_status.emit(section_index, "Done", output_name)
                        self.log.emit(f"Finished section: {self._display_name(section)} -> {output_name}")
                    else:
                        self.section_done.emit(f"{self._display_name(section)} (no audio returned)")
                        self.section_status.emit(section_index, "Failed", "No audio returned")
                        self.log.emit(f"No audio returned for {self._display_name(section)}")

            if not wrote_any_audio:
                self.failed.emit("No audio was generated from selected chapters.")
                return

            if cfg.embed_m4b_chapters and chapter_marks:
                self.now_doing.emit("Writing M4B chapter metadata...")
                ffmeta_path = out_dir / f"{book_base}.chapters.ffmeta"
                self._write_ffmpeg_chapter_metadata(
                    ffmeta_path=ffmeta_path,
                    chapter_marks=chapter_marks,
                    sample_rate=engine.cfg.sample_rate,
                )
                self.log.emit(f"Wrote chapter metadata: {ffmeta_path.name}")
            else:
                ffmeta_path = None

            self.now_doing.emit("Encoding M4B...")
            self._encode_m4b(
                input_wav=temp_wav_path,
                output_m4b=m4b_path,
                aac_bitrate=cfg.aac_bitrate,
                ffmeta=ffmeta_path,
            )
            self.section_done.emit(f"M4B ready -> {m4b_path.name}")
            self.log.emit(f"M4B ready: {m4b_path}")

            elapsed = max(time.time() - start, 1e-6)
            final_cps = total_chars / elapsed
            self.progress.emit(total_chars, total_chars, final_cps, 0.0, total_chunks, total_chunks)
            self.now_doing.emit("Done.")
            self.finished.emit(str(out_dir))

        except Exception as e:
            self.log.emit(f"ERROR | {type(e).__name__}: {e}")
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
        self.log.emit("Cancellation requested.")

    def set_paused(self, paused: bool) -> None:
        self._pause_requested = paused
        self.log.emit("Pause requested." if paused else "Resume requested.")

    def _check_control_flags(self) -> None:
        while self._pause_requested and not self._cancel:
            if not self._is_paused:
                self._is_paused = True
                self.paused.emit(True)
                self.now_doing.emit("Paused. Waiting to resume...")
            time.sleep(0.1)
        if self._is_paused:
            self._is_paused = False
            self.paused.emit(False)

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
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", section.title).strip("_") or "untitled"
        return f"{safe_title}.wav"

    def _chapter_title(self, index: int, section: Section) -> str:
        return section.title

    def _spoken_part_header(self, index: int, section: Section, part_number: int) -> str:
        return f"Chapter {index + 1}. {section.title}. Part {part_number}."

    @classmethod
    def _chunk_text(cls, text: str, max_chars: int = 700) -> List[str]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
        if not paragraphs:
            return cls._split_long_sentence(normalized, max_chars)

        chunks: List[str] = []
        current = ""

        for paragraph in paragraphs:
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if not paragraph:
                continue

            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(cls._chunk_paragraph_by_sentences(paragraph, max_chars=max_chars))
                continue

            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                current = paragraph

        if current:
            chunks.append(current.strip())

        return [c for c in chunks if c.strip()]

    @classmethod
    def _chunk_paragraph_by_sentences(cls, paragraph: str, max_chars: int) -> List[str]:
        sentences = cls._split_into_sentences(paragraph)
        if not sentences:
            return cls._split_long_sentence(paragraph, max_chars)

        chunks: List[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(sentence) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(cls._split_long_sentence(sentence, max_chars))
                continue

            candidate = sentence if not current else f"{current} {sentence}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence

        if current:
            chunks.append(current.strip())

        return chunks

    @staticmethod
    def _split_into_sentences(text: str) -> List[str]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []
        parts = re.split(r"(?<=[.!?…])\s+(?=[A-Z0-9\"'\[(])", text)
        cleaned = [p.strip() for p in parts if p.strip()]
        return cleaned if cleaned else [text]

    @staticmethod
    def _split_long_sentence(text: str, max_chars: int) -> List[str]:
        words = text.split()
        if not words:
            return []

        chunks: List[str] = []
        current_words: List[str] = []
        current_len = 0

        for word in words:
            add_len = len(word) if current_len == 0 else len(word) + 1
            if current_words and current_len + add_len > max_chars:
                chunks.append(" ".join(current_words).strip())
                current_words = [word]
                current_len = len(word)
            else:
                current_words.append(word)
                current_len += add_len

        if current_words:
            chunks.append(" ".join(current_words).strip())

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

        lines: List[str] = [";FFMETADATA1"]
        for start_samp, end_samp, title in chapter_marks:
            start_ms = max(0, samp_to_ms(start_samp))
            end_ms = max(start_ms + 1, samp_to_ms(end_samp))
            safe_title = title.replace("\n", " ").strip()
            lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={safe_title}",
            ])
        ffmeta_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _encode_m4b(
        input_wav: Path,
        output_m4b: Path,
        aac_bitrate: str,
        ffmeta: Optional[Path],
    ) -> None:
        ffmpeg = JobWorker._resolve_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg was not found. This app expects a bundled ffmpeg binary or ffmpeg on PATH. "
                "Run scripts/download_offline_assets.py or use the Releases package. "
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
    def _resolve_ffmpeg() -> Optional[str]:
        candidates = [
            bundled_path("assets", "ffmpeg", "ffmpeg.exe"),
            bundled_path("assets", "ffmpeg", "ffmpeg"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return shutil.which("ffmpeg")

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
