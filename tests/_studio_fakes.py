"""Test doubles for the studio: a fake recogniser and a ready-made EngineWorker."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Callable, List, Optional

from manga_extract.studio.workers import EngineWorker


def fake_result():
    """Shaped like ``pipeline.PageResult``: one vertical and one horizontal block."""
    vertical = SimpleNamespace(
        xyxy=[500, 50, 560, 300], vertical=True, language="ja", text="ふはは",
        lines=[SimpleNamespace(polygon=[[500, 50], [560, 50], [560, 300], [500, 300]],
                               text="ふはは")])
    horizontal = SimpleNamespace(
        xyxy=[100, 600, 300, 680], vertical=False, language="ja", text="こんにちは",
        lines=[SimpleNamespace(polygon=[[100, 600], [300, 600], [300, 680], [100, 680]],
                               text="こんにちは")])
    return SimpleNamespace(width=713, height=861, blocks=[vertical, horizontal])


class FakeExtractor:
    """Records calls; can block on ``gate`` so tests control job timing."""

    def __init__(self, gate: Optional[threading.Event] = None,
                 fail_for: Optional[Callable[[object], bool]] = None) -> None:
        self.gate = gate
        self.fail_for = fail_for
        self.images: List[object] = []

    def extract_page(self, image, on_progress=None):
        self.images.append(image)
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.fail_for is not None and self.fail_for(image):
            raise RuntimeError("boom")
        if on_progress:
            on_progress("detect", 2, 3)
            on_progress("ocr", 3, 3)
        return fake_result()


def make_engine(extractor: Optional[FakeExtractor] = None, load_error: str = "",
                info: str = "检测 Fake · OCR cpu") -> EngineWorker:
    """An EngineWorker whose 'models' are instant (or fail to load)."""
    extractor = extractor or FakeExtractor()

    def factory():
        if load_error:
            raise RuntimeError(load_error)
        return extractor, info

    # The 'image' handed to the extractor is simply the path, so tests can tell pages apart.
    return EngineWorker(factory, image_reader=lambda path: path)


def wait_until(app, predicate, timeout: float = 8.0) -> bool:
    """Pump the Qt event loop until ``predicate()`` holds (queued signals need it)."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def type_text(widget, text: str) -> None:
    """Deliver text the way an input method commits it.

    ``QTest.keyClicks`` must not be used for CJK: it crashes the whole process.
    """
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtWidgets import QApplication

    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(widget, event)


class FakeTranslator:
    """Prefixes every text; can block on ``gate`` or fail, and records each call."""

    def __init__(self, gate=None, fail=False, prefix="译:"):
        self.gate, self.fail, self.prefix = gate, fail, prefix
        self.calls = []

    def translate(self, texts):
        self.calls.append(list(texts))
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.fail:
            from manga_extract.translation import TranslationError

            raise TranslationError("boom")
        return [self.prefix + text for text in texts]


def dispose_widget(app, widget) -> None:
    """Destroy a widget *now*.

    A closed widget is merely hidden, and ``deleteLater`` alone waits for some later event
    loop. Leftovers are not free: qfluentwidgets restyles every live widget on each theme
    change, so a test that leaves a panel of text boxes behind slows down every later test
    that builds a window (a 5x slowdown was measured).
    """
    from PySide6.QtCore import QEvent
    from shiboken6 import isValid

    if not isValid(widget):
        return                                  # already destroyed (a test may dispose it itself)
    widget.hide()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def dispose_window(app, window) -> None:
    """Destroy a MainWindow *now* (also stopping its debounce timers first)."""
    for name in ("_overlay_timer", "_save_timer"):
        timer = getattr(window, name, None)
        if timer is not None:
            timer.stop()
    dispose_widget(app, window)


def tempdir(test) -> "Path":
    """A temp folder removed after the test, tolerating Windows' brief file locks.

    A page image that was just copied in can be held for a moment by the virus scanner or the
    indexer; deleting right away then fails with WinError 32, so retry for a short while.
    """
    import shutil
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp(prefix="studio_test_"))

    def cleanup() -> None:
        for attempt in range(10):
            try:
                shutil.rmtree(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.1 * (attempt + 1))
        shutil.rmtree(path, ignore_errors=True)

    test.addCleanup(cleanup)
    return path
