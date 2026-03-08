from __future__ import annotations

import os
import sys


def _ensure_stdio() -> None:
    """
    Make sure stdout/stderr exist before importing GUI code or
    third-party packages that configure logging during import.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_ensure_stdio()

from abtts.app import run_app


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()