from __future__ import annotations

from pathlib import Path
import soundfile as sf

from abtts.tts.kokoro_engine import KokoroEngine, KokoroConfig


def main() -> None:
    out_dir = Path("data/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    text = (
        "This is a Kokoro smoke test. "
        "If you can hear this clearly, your local setup works."
        "You are a really suspicious person my little friend."
    )

    engine = KokoroEngine(KokoroConfig(lang_code="a", voice="af_heart"))
    audio = engine.synthesize_one(text)

    out_path = out_dir / "smoke_test1.wav"
    sf.write(out_path, audio, 24000)
    print(f"Wrote: {out_path.resolve()}")


if __name__ == "__main__":
    main()