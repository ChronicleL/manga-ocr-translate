"""Background fetching for the web-import dialog (Qt signals on top of ``web_import``)."""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

from .web_import import ImageCandidate, WebImportError, download_image, fetch_page

THUMB_SIZE = (170, 230)


class DaemonPool:
    """A few daemon threads fed from a queue.

    Not ``ThreadPoolExecutor``: its threads are joined at interpreter exit, so a download that
    is stuck on a slow server would delay closing the application by its full timeout.
    """

    def __init__(self, workers: int = 4) -> None:
        self._jobs: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._workers = workers
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, fn: Callable, *args) -> None:
        with self._lock:
            if not self._threads:
                for number in range(self._workers):
                    thread = threading.Thread(target=self._run, name=f"web-{number}", daemon=True)
                    thread.start()
                    self._threads.append(thread)
        self._jobs.put((fn, args))

    def clear_pending(self) -> None:
        """Drop jobs that have not started yet (running ones cannot be interrupted)."""
        try:
            while True:
                self._jobs.get_nowait()
        except queue.Empty:
            pass

    def _run(self) -> None:
        while True:
            fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - a job must never kill its worker thread
                traceback.print_exc(file=sys.stderr)


class WebSignals(QObject):
    pageFetched = Signal(str, object)                   # final URL, [ImageCandidate]
    fetchFailed = Signal(str)
    imageLoaded = Signal(int, QImage, int, int, int, str, str)   # index, thumb, w, h, bytes, path, suffix
    imageFailed = Signal(int, str)                       # index, message


class WebImporter:
    """Fetch a page, then download its images in parallel into a folder.

    Every call that starts new work (or ``cancel``) bumps a generation counter; results of
    older work are dropped instead of being delivered, so a stale download never lands on a
    freshly built list.
    """

    def __init__(self, workers: int = 4, thumb_size=THUMB_SIZE) -> None:
        self.signals = WebSignals()
        self._pool = DaemonPool(workers)
        self._thumb_size = thumb_size
        self._generation = 0
        self._lock = threading.Lock()

    def _next_generation(self) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._pool.clear_pending()
        return generation

    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def cancel(self) -> None:
        self._next_generation()

    def fetch_page(self, url: str) -> None:
        generation = self._next_generation()
        self._pool.submit(self._fetch, generation, url)

    def load_images(self, candidates: Sequence[ImageCandidate], referer: Optional[str],
                    folder: Path) -> None:
        generation = self._next_generation()
        Path(folder).mkdir(parents=True, exist_ok=True)
        for index, candidate in enumerate(candidates):
            self._pool.submit(self._load, generation, index, candidate.url, referer, Path(folder))

    # -- worker side --------------------------------------------------------
    def _fetch(self, generation: int, url: str) -> None:
        try:
            page = fetch_page(url)
        except WebImportError as exc:
            if self._current(generation):
                self.signals.fetchFailed.emit(str(exc))
            return
        if self._current(generation):
            self.signals.pageFetched.emit(page.url, page.candidates)

    def _load(self, generation: int, index: int, url: str, referer: Optional[str],
              folder: Path) -> None:
        if not self._current(generation):
            return
        try:
            downloaded = download_image(url, referer)
            path = folder / f"{index:04d}{downloaded.suffix}"
            path.write_bytes(downloaded.data)
            image = QImage.fromData(downloaded.data)
            if image.isNull():
                raise WebImportError("无法解码这张图片")
            thumb = image.scaled(self._thumb_size[0], self._thumb_size[1],
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            size = (image.width(), image.height())
        except (WebImportError, OSError) as exc:
            if self._current(generation):
                self.signals.imageFailed.emit(index, str(exc))
            return
        if self._current(generation):
            self.signals.imageLoaded.emit(index, thumb, size[0], size[1], len(downloaded.data),
                                          str(path), downloaded.suffix)
