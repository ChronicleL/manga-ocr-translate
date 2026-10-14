"""Launch Manga Studio (the project-based translation workbench).

Double-click ``run_studio.bat`` or run this file. The older floating tool is
still available through ``run_gui.py`` until the studio reaches feature parity.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_environment() -> None:
    # Prefer the Hugging Face mirror for the one-time OCR model download.
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # A windowed (pythonw) run has no console: point stdout/stderr at devnull
    # so libraries that log (loguru, transformers) cannot fail on write.
    if sys.stdout is None or sys.stderr is None:
        stream = open(os.devnull, "w", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


def _add_source_root() -> None:
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))


_prepare_environment()
_add_source_root()

from manga_extract.studio.app import run  # noqa: E402

if __name__ == "__main__":
    run()
