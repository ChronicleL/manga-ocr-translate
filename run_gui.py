"""Launch the floating manga text extractor window.

Double-click ``run_gui.bat`` (or run this file) to start it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from manga_extract.gui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
