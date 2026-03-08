from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from kokoro import KPipeline  # official API shown in Kokoro docs :contentReference[oaicite:3]{index=3}
from kokoro.model import KModel

from abtts.runtime_paths import bundled_path, env_truthy, is_frozen


@dataclass(frozen=True)
class KokoroConfig:
    lang_code: str = "a"      # 'a' American English, 'b' British, etc. :contentReference[oaicite:4]{index=4}
    voice: str = "af_heart"   # example voice used in docs :contentReference[oaicite:5]{index=5}
    sample_rate: int = 24000  # Kokoro examples use 24000 Hz :contentReference[oaicite:6]{index=6}


class KokoroEngine:
    def __init__(self, cfg: KokoroConfig):
        self.cfg = cfg
        self._offline = env_truthy("ABTTS_OFFLINE", default=is_frozen())
        self._repo_id = "hexgrad/Kokoro-82M"
        self._assets_root = bundled_path("assets", "kokoro")

        model = None
        if self._assets_root.exists():
            model = self._load_local_model(self._assets_root)

        if model is None and self._offline:
            raise RuntimeError(
                "Offline mode is enabled, but Kokoro assets are missing. "
                "Expected files under assets/kokoro (config.json, kokoro-v1_0.pth, voices/*.pt). "
                "Run scripts/download_offline_assets.py or use the Releases package."
            )

        self.pipeline = KPipeline(lang_code=cfg.lang_code, repo_id=self._repo_id, model=model or True)

    def _load_local_model(self, assets_root: Path) -> KModel | None:
        config_path = assets_root / "config.json"
        model_path = assets_root / "kokoro-v1_0.pth"
        voices_dir = assets_root / "voices"
        if not (config_path.exists() and model_path.exists() and voices_dir.exists()):
            return None
        return KModel(repo_id=self._repo_id, config=str(config_path), model=str(model_path))

    def _resolve_voice(self, voice: str) -> str:
        if voice.endswith(".pt"):
            return voice
        local_voice = self._assets_root / "voices" / f"{voice}.pt"
        if local_voice.exists():
            return str(local_voice)
        if self._offline:
            raise RuntimeError(
                f"Voice '{voice}' was not found in bundled assets at {local_voice}. "
                "Run scripts/download_offline_assets.py or use the Releases package."
            )
        return voice

    def synthesize_stream(self, text: str) -> Iterable[Tuple[int, np.ndarray]]:
        """
        Yields (chunk_index, audio_float32_array).
        Kokoro returns a generator of (gs, ps, audio) in its examples. :contentReference[oaicite:7]{index=7}
        """
        voice = self._resolve_voice(self.cfg.voice)
        gen = self.pipeline(text, voice=voice)
        for i, (_gs, _ps, audio) in enumerate(gen):
            # audio is typically a float array already; normalize type for safety
            yield i, np.asarray(audio, dtype=np.float32)

    def synthesize_one(self, text: str) -> np.ndarray:
        chunks = [audio for _i, audio in self.synthesize_stream(text)]
        if not chunks:
            return np.zeros((0,), dtype=np.float32)
        return np.concatenate(chunks)
