"""``--selftest``: start, wait for the recognition models to load, write the verdict, exit.

For checking a build (or a fresh install) without clicking anything:

    MangaStudio.exe --selftest result.txt          (set QT_QPA_PLATFORM=offscreen on a headless box)

``result.txt`` gets one line: ``OK <backend description>`` or ``FAIL <reason>``; the exit code
is 0 or 1. A windowed (no console) executable has no stdout, hence the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

DEFAULT_TIMEOUT_MS = 240_000


class SelfTest(QObject):
    """Watches the engine of ``window``; the first verdict wins."""

    def __init__(self, window, result_path: str | Path,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        super().__init__(window)
        self.path = Path(result_path)
        self.exit_code: Optional[int] = None
        signals = window.engine.signals
        signals.loaded.connect(self._on_loaded)
        signals.loadFailed.connect(self._on_failed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start()

    @property
    def finished(self) -> bool:
        return self.exit_code is not None

    def _finish(self, code: int, line: str) -> None:
        if self.finished:
            return
        self.exit_code = code
        self._timer.stop()
        try:
            self.path.write_text(line + "\n", encoding="utf-8")
        except OSError:
            self.exit_code = 1                      # a verdict nobody can read is a failure
        QApplication.exit(self.exit_code)

    def _on_loaded(self, info: str) -> None:
        self._finish(0, f"OK {info}")

    def _on_failed(self, message: str) -> None:
        first = message.strip().splitlines()[0] if message.strip() else "unknown error"
        self._finish(1, f"FAIL {first}")

    def _on_timeout(self) -> None:
        self._finish(1, "FAIL timed out while loading the models")


def parse_selftest(argv: list) -> Optional[str]:
    """The result path after ``--selftest``, or None when the flag is absent / has no path."""
    if "--selftest" not in argv:
        return None
    index = argv.index("--selftest")
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        return None
    return argv[index + 1]
