"""Launch the floating manga text extractor window.

Double-click ``run_gui.bat`` (or run this file) to start it. The packaged
executable uses this same entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_environment() -> None:
    # Prefer the Hugging Face mirror for the one-time OCR model download.
    # An already-set HF_ENDPOINT wins.
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # A windowed build has no console, so sys.stdout/stderr are None and any
    # library that logs (loguru, transformers) would fail when writing. Point
    # them at a log file next to the executable, falling back to devnull.
    if sys.stdout is not None and sys.stderr is not None:
        return
    target = os.devnull
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "manga_extract.log"
        try:
            candidate.touch()
            target = str(candidate)
        except OSError:
            pass
    stream = open(target, "w", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _add_source_root() -> None:
    if getattr(sys, "frozen", False):
        return  # PyInstaller already configured sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))


_prepare_environment()
_add_source_root()

from manga_extract.gui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
