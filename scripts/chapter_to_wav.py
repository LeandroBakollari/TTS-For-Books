from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

import numpy as np
import soundfile as sf

from abtts.tts.kokoro_engine import KokoroEngine, KokoroConfig


# ----------------------------
# Text processing / chunking
# ----------------------------

@dataclass(frozen=True)
class ChunkingConfig:
    max_chars: int = 650
    min_chars: int = 250
    keep_paragraph_break: bool = True


@dataclass(frozen=True)
class PauseConfig:
    base_pause_s: float = 0.12
    quote_pause_s: float = 0.22        # longer pause after a chunk ending in a quote
    dialogue_comma_pause_s: float = 0.16  # pause for dialogue endings like: ," or ,"
    paragraph_pause_s: float = 0.18    # extra pause when we flush at paragraph breaks


def normalize_text(s: str) -> str:
    # Normalize newlines
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # Normalize common “smart quotes” to ASCII to simplify rules.
    # (This keeps behavior predictable.)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    # Collapse tabs/spaces
    s = re.sub(r"[ \t]+", " ", s)

    # Trim trailing spaces per line
    s = "\n".join(line.strip() for line in s.split("\n"))

    # Collapse 3+ newlines to 2 (keep paragraph separation)
    s = re.sub(r"\n{3,}", "\n\n", s)

    return s.strip()


def split_into_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# Sentence boundary rules:
# 1) Strong endings: . ! ? (optionally followed by a closing quote)
# 2) Dialogue endings: , followed by a closing quote (",) -> short boundary
#
# We'll split by scanning and cutting at those boundaries.
_STRONG_END_RE = re.compile(r'[.!?]+(?:"|\'|”|’)?\s+')
_DIALOGUE_COMMA_END_RE = re.compile(r',(?:"|\'|”|’)\s+')


def split_paragraph_into_sentences(paragraph: str) -> List[Tuple[str, str]]:
    """
    Returns list of (sentence_text, boundary_type)
    boundary_type in {"strong", "dialogue_comma", "none"}.

    We mark boundary type so we can add better pauses later.
    """
    p = paragraph.strip()
    if not p:
        return []

    cuts: List[Tuple[int, str]] = []  # (cut_index, boundary_type)

    for m in _STRONG_END_RE.finditer(p):
        cuts.append((m.end(), "strong"))

    for m in _DIALOGUE_COMMA_END_RE.finditer(p):
        cuts.append((m.end(), "dialogue_comma"))

    # Sort cuts; keep stable unique
    cuts.sort(key=lambda x: x[0])

    out: List[Tuple[str, str]] = []
    last = 0
    for end, btype in cuts:
        if end <= last:
            continue
        seg = p[last:end].strip()
        if seg:
            out.append((seg, btype))
        last = end

    tail = p[last:].strip()
    if tail:
        out.append((tail, "none"))

    return out


def pack_sentences_into_chunks(
    paragraphs: List[str],
    cfg: ChunkingConfig,
) -> List[Tuple[str, str]]:
    """
    Returns list of (chunk_text, chunk_boundary_hint)
    chunk_boundary_hint is the strongest boundary encountered at the end:
      "strong" / "dialogue_comma" / "none" / "paragraph"
    """
    chunks: List[Tuple[str, str]] = []
    current = ""
    current_hint = "none"

    def flush(hint: str | None = None):
        nonlocal current, current_hint
        if current.strip():
            chunks.append((current.strip(), hint or current_hint))
        current = ""
        current_hint = "none"

    def hint_priority(h: str) -> int:
        return {"none": 0, "dialogue_comma": 1, "strong": 2, "paragraph": 3}.get(h, 0)

    for para in paragraphs:
        sentences = split_paragraph_into_sentences(para)

        for sent, btype in sentences:
            # if adding would exceed max, flush first
            if current and (len(current) + 1 + len(sent) > cfg.max_chars):
                flush()

            current = (current + " " + sent).strip() if current else sent

            # keep the strongest boundary hint seen near the end
            if hint_priority(btype) >= hint_priority(current_hint):
                current_hint = btype

        if cfg.keep_paragraph_break:
            # Paragraph break is a strong pacing cue
            flush(hint="paragraph")

    flush()

    # Post-pass merge small chunks if possible
    merged: List[Tuple[str, str]] = []
    i = 0
    while i < len(chunks):
        text_i, hint_i = chunks[i]
        if len(text_i) < cfg.min_chars and i + 1 < len(chunks):
            text_n, hint_n = chunks[i + 1]
            if len(text_i) + 1 + len(text_n) <= cfg.max_chars:
                # Preserve the stronger hint
                stronger = hint_n if hint_n != "none" else hint_i
                merged.append(((text_i + " " + text_n).strip(), stronger))
                i += 2
                continue
        merged.append((text_i, hint_i))
        i += 1

    return merged


# ----------------------------
# WAV streaming + progress
# ----------------------------

def float_to_int16(audio: np.ndarray) -> np.ndarray:
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767.0).astype(np.int16)


def write_silence(writer: sf.SoundFile, seconds: float, sample_rate: int) -> None:
    if seconds <= 0:
        return
    n = int(seconds * sample_rate)
    if n <= 0:
        return
    writer.write(np.zeros((n,), dtype=np.int16))


def pause_for_hint(hint: str, pause_cfg: PauseConfig) -> float:
    if hint == "paragraph":
        return max(pause_cfg.paragraph_pause_s, pause_cfg.base_pause_s)
    if hint == "strong":
        return pause_cfg.base_pause_s
    if hint == "dialogue_comma":
        return pause_cfg.dialogue_comma_pause_s
    return pause_cfg.base_pause_s


def synthesize_txt_to_wav(
    input_txt: Path,
    output_wav: Path,
    voice: str = "af_heart",
    lang_code: str = "a",
    sample_rate: int = 24000,
    chunk_cfg: ChunkingConfig = ChunkingConfig(),
    pause_cfg: PauseConfig = PauseConfig(),
) -> None:
    text = input_txt.read_text(encoding="utf-8", errors="ignore")
    text = normalize_text(text)

    paragraphs = split_into_paragraphs(text)
    chunks = pack_sentences_into_chunks(paragraphs, chunk_cfg)

    print(f"Input:  {input_txt}")
    print(f"Output: {output_wav}")
    print(f"Paragraphs: {len(paragraphs)} | Chunks: {len(chunks)}")
    print(f"Voice: {voice} | Lang: {lang_code} | SR: {sample_rate}")

    engine = KokoroEngine(
        KokoroConfig(lang_code=lang_code, voice=voice, sample_rate=sample_rate)
    )

    output_wav.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    chars_done = 0
    total_chars = sum(len(c[0]) for c in chunks)

    with sf.SoundFile(
        output_wav,
        mode="w",
        samplerate=sample_rate,
        channels=1,
        subtype="PCM_16",
    ) as writer:
        for idx, (chunk, hint) in enumerate(chunks, start=1):
            # Generate and stream-write audio
            for _i, audio_f32 in engine.synthesize_stream(chunk):
                writer.write(float_to_int16(audio_f32))

            # Add pacing pause based on boundary hint
            pause_s = pause_for_hint(hint, pause_cfg)

            # Extra: if chunk literally ends with a quote, make it a bit longer
            if chunk.rstrip().endswith('"'):
                pause_s = max(pause_s, pause_cfg.quote_pause_s)

            write_silence(writer, pause_s, sample_rate)

            chars_done += len(chunk)
            elapsed = time.time() - start
            cps = chars_done / elapsed if elapsed > 0 else 0.0
            pct = (chars_done / total_chars * 100.0) if total_chars else 0.0

            print(
                f"[{idx:03d}/{len(chunks):03d}] {pct:6.2f}% | {cps:8.1f} chars/s | "
                f"hint={hint:13s} | pause={pause_s:.2f}s | chunk chars={len(chunk)}"
            )

    print(f"Done in {time.time() - start:.1f}s → {output_wav.resolve()}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert a chapter TXT into one WAV using Kokoro (streaming + dialogue-aware pauses)."
    )
    parser.add_argument("input_txt", type=str)
    parser.add_argument("output_wav", type=str)
    parser.add_argument("--voice", type=str, default="af_heart")
    parser.add_argument("--lang", type=str, default="a")
    parser.add_argument("--max-chars", type=int, default=650)
    parser.add_argument("--min-chars", type=int, default=250)

    # pause tuning
    parser.add_argument("--base-pause", type=float, default=0.12)
    parser.add_argument("--quote-pause", type=float, default=0.22)
    parser.add_argument("--dialogue-comma-pause", type=float, default=0.16)
    parser.add_argument("--paragraph-pause", type=float, default=0.18)

    args = parser.parse_args()

    synthesize_txt_to_wav(
        input_txt=Path(args.input_txt),
        output_wav=Path(args.output_wav),
        voice=args.voice,
        lang_code=args.lang,
        chunk_cfg=ChunkingConfig(max_chars=args.max_chars, min_chars=args.min_chars),
        pause_cfg=PauseConfig(
            base_pause_s=args.base_pause,
            quote_pause_s=args.quote_pause,
            dialogue_comma_pause_s=args.dialogue_comma_pause,
            paragraph_pause_s=args.paragraph_pause,
        ),
    )


if __name__ == "__main__":
    # Older laptops often do better with limited threads
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    main()