from __future__ import annotations

import os
import sys
import multiprocessing


def _ensure_stdio() -> None:
    """
    In PyInstaller --windowed builds on Windows, sys.stdout/sys.stderr
    may be None. Some third-party libraries (like loguru/kokoro) expect
    valid file-like streams during import-time logging setup.

    We redirect missing streams to os.devnull so imports do not crash.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_ensure_stdio()
multiprocessing.freeze_support()

from abtts.cli import main


if __name__ == "__main__":
    main()
