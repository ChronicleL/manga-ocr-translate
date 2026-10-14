"""Background engine thread: loads the models once, then recognises pages in order.

A plain daemon thread (not a QThread) is used on purpose: recognition can take
a long time inside torch / onnxruntime and cannot be interrupted, and a daemon
thread never blocks the window from closing.

Results travel back through Qt signals, which are queued to the GUI thread
when emitted from this thread.

Each submitted job carries a token. Submitting a page again, or discarding it,
invalidates older jobs for that page id, so a page that was deleted (or whose
id was reused by a newer import) never receives a stale result.
"""

from __future__ import annotations

import gc
import itertools
import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from PySide6.QtCore import QObject, Signal

from .project import PageData

# factory() -> (extractor, backend_info_text); runs on the worker thread.
ExtractorFactory = Callable[[], Tuple[Any, str]]
ImageReader = Callable[[str], Any]


def default_extractor_factory(model_path: Path, engine_config_path: Path,
                              hf_endpoint: Optional[str] = None) -> ExtractorFactory:
    """Build the real detector + OCR (heavy imports happen here, off the GUI thread)."""

    def build() -> Tuple[Any, str]:
        from ..detector import ComicTextDetector
        from ..pipeline import MangaExtractor
        from ..recognizer import MangaOcrRecognizer
        from ..runtime import EngineConfig

        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"找不到检测模型：{model_path}\n请先运行 scripts/download_models.py")
        engine = EngineConfig.load(engine_config_path)
        detector = ComicTextDetector(model_path, device=engine.device, threads=engine.threads)
        recognizer = MangaOcrRecognizer(
            hf_endpoint=hf_endpoint, device=engine.device,
            threads=engine.threads, batch_size=engine.ocr_batch)
        extractor = MangaExtractor(detector=detector, recognizer=recognizer)
        backend = detector.providers[0].replace("ExecutionProvider", "")
        if backend == "CPU":
            backend += "（未启用 GPU 加速）"
        return extractor, f"检测 {backend} · OCR {recognizer.device}"

    return build


def default_image_reader(path: str) -> Any:
    from ..image_io import imread

    return imread(path)


class TranslationSignals(QObject):
    translated = Signal(int, str, object)   # job id, page id, [(block index, source, translation)]
    failed = Signal(int, str, str)          # job id, page id, message


class TranslationWorker:
    """Runs translation requests one at a time on a daemon thread.

    A network call cannot be interrupted, so cancelling just bumps a generation
    counter: jobs queued under an older generation are skipped and a running
    one is not reported. (Daemon: a slow request never delays closing the app.)
    """

    def __init__(self) -> None:
        self.signals = TranslationSignals()
        self._jobs: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._lock = threading.Lock()
        self._generation = 0
        self._thread: Optional[threading.Thread] = None

    def submit(self, job_id: int, page_id: str, items: list, translator: Any) -> None:
        """``items`` is ``[(block index, source text)]``; ``translator`` is built by the caller."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="translate", daemon=True)
            self._thread.start()
        with self._lock:
            generation = self._generation
        self._jobs.put((generation, job_id, page_id, list(items), translator))

    def cancel_all(self) -> None:
        with self._lock:
            self._generation += 1

    def stop(self) -> None:
        self._jobs.put(None)

    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            generation, job_id, page_id, items, translator = job
            if not self._current(generation):
                continue
            try:
                sources = [source for _index, source in items]
                outputs = list(translator.translate(sources))
                if len(outputs) != len(sources):
                    raise RuntimeError(
                        f"译文数量（{len(outputs)}）与原文（{len(sources)}）不一致")
            except Exception as exc:  # noqa: BLE001 - reported to the user
                if self._current(generation):
                    # Drop the rest of this batch right here: waiting for the GUI to react would
                    # let the next queued request go out first (same cause, e.g. a bad API key).
                    self.cancel_all()
                    self.signals.failed.emit(job_id, page_id, str(exc) or type(exc).__name__)
                continue
            if self._current(generation):
                result = [(index, source, str(text).strip())
                          for (index, source), text in zip(items, outputs)]
                self.signals.translated.emit(job_id, page_id, result)


class EngineSignals(QObject):
    loaded = Signal(str)                 # backend description
    loadFailed = Signal(str)
    started = Signal(str)                # page id: this page is now being recognised
    progress = Signal(str, str, int, int)  # page id, stage ("detect" | "ocr"), done, total
    recognized = Signal(str, object)     # page id, PageData
    failed = Signal(str, str)            # page id, message


class EngineWorker:
    """Serial recognition queue backed by one daemon thread."""

    def __init__(self, extractor_factory: ExtractorFactory,
                 image_reader: ImageReader = default_image_reader) -> None:
        self.signals = EngineSignals()
        self._factory = extractor_factory
        self._read_image = image_reader
        self._jobs: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._lock = threading.Lock()
        self._tokens: Dict[str, int] = {}     # page id -> token of its live job
        self._pending: Set[str] = set()       # queued, not started yet
        self._counter = itertools.count(1)
        self._extractor: Any = None
        self._load_error = ""
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)

    # -- public API (GUI thread) -------------------------------------------
    def start(self) -> None:
        """Start the thread; model loading is the first job in the queue."""
        self._thread.start()
        self._jobs.put(("load",))

    def reload(self) -> None:
        """Load the models again (after the performance settings changed).

        It is queued like any job: pages already waiting run after the new models are ready.
        """
        self._jobs.put(("load",))

    def submit(self, page_id: str, image_path: str | Path) -> None:
        with self._lock:
            token = next(self._counter)
            self._tokens[page_id] = token
            self._pending.add(page_id)
        self._jobs.put(("recognize", page_id, str(image_path), token))

    def discard(self, page_id: str) -> None:
        """Forget a page: queued jobs are skipped, a running one is not reported."""
        with self._lock:
            self._tokens.pop(page_id, None)
            self._pending.discard(page_id)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def stop(self) -> None:
        """Ask the thread to exit once its current job ends (daemon: never blocks shutdown)."""
        self._jobs.put(None)

    # -- worker thread ------------------------------------------------------
    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            if job[0] == "load":
                self._load()
            else:
                self._recognize(*job[1:])

    def _load(self) -> None:
        # Drop the old models first: holding two copies of the OCR model would double the peak.
        self._extractor = None
        self._load_error = ""
        gc.collect()
        try:
            self._extractor, info = self._factory()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self._load_error = f"{type(exc).__name__}: {exc}"
            self.signals.loadFailed.emit(f"{self._load_error}\n\n{traceback.format_exc()}")
            return
        self.signals.loaded.emit(info)

    def _live(self, page_id: str, token: int) -> bool:
        with self._lock:
            return self._tokens.get(page_id) == token

    def _recognize(self, page_id: str, image_path: str, token: int) -> None:
        with self._lock:
            if self._tokens.get(page_id) != token:
                return                      # superseded or discarded while queued
            self._pending.discard(page_id)
        if self._extractor is None:
            self.signals.failed.emit(page_id, f"识别引擎未能加载：{self._load_error or '请稍候'}")
            return
        self.signals.started.emit(page_id)
        try:
            image = self._read_image(image_path)
            if image is None:
                raise OSError(f"无法读取图片：{image_path}")

            def on_progress(stage: str, done: int, total: int) -> None:
                if self._live(page_id, token):
                    self.signals.progress.emit(page_id, stage, done, total)

            result = self._extractor.extract_page(image, on_progress=on_progress)
            data = PageData.from_result(result)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            if self._live(page_id, token):
                self.signals.failed.emit(page_id, f"{type(exc).__name__}: {exc}")
            return
        if self._live(page_id, token):
            with self._lock:
                self._tokens.pop(page_id, None)
            self.signals.recognized.emit(page_id, data)
