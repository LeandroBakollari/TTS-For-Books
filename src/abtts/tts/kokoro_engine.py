from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

import numpy as np
from kokoro import KPipeline  # official API shown in Kokoro docs :contentReference[oaicite:3]{index=3}


@dataclass(frozen=True)
class KokoroConfig:
    lang_code: str = "a"      # 'a' American English, 'b' British, etc. :contentReference[oaicite:4]{index=4}
    voice: str = "af_heart"   # example voice used in docs :contentReference[oaicite:5]{index=5}
    sample_rate: int = 24000  # Kokoro examples use 24000 Hz :contentReference[oaicite:6]{index=6}


class KokoroEngine:
    def __init__(self, cfg: KokoroConfig):
        self.cfg = cfg
        self.pipeline = KPipeline(lang_code=cfg.lang_code)

    def synthesize_stream(self, text: str) -> Iterable[Tuple[int, np.ndarray]]:
        """
        Yields (chunk_index, audio_float32_array).
        Kokoro returns a generator of (gs, ps, audio) in its examples. :contentReference[oaicite:7]{index=7}
        """
        gen = self.pipeline(text, voice=self.cfg.voice)
        for i, (_gs, _ps, audio) in enumerate(gen):
            # audio is typically a float array already; normalize type for safety
            yield i, np.asarray(audio, dtype=np.float32)

    def synthesize_one(self, text: str) -> np.ndarray:
        chunks = [audio for _i, audio in self.synthesize_stream(text)]
        if not chunks:
            return np.zeros((0,), dtype=np.float32)
        return np.concatenate(chunks)