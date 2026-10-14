"""EngineWorker: queue order, discard/supersede semantics and thread hand-off."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import FakeExtractor, make_engine, wait_until

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


if HAVE_QT:
    class Collector(QObject):
        """Receives worker signals through bound methods, exactly like MainWindow does."""

        def __init__(self, signals):
            super().__init__()
            self.events = []          # (kind, args)
            self.threads = set()
            signals.loaded.connect(self.on_loaded)
            signals.loadFailed.connect(self.on_load_failed)
            signals.started.connect(self.on_started)
            signals.progress.connect(self.on_progress)
            signals.recognized.connect(self.on_recognized)
            signals.failed.connect(self.on_failed)

        def _record(self, kind, *args):
            self.threads.add(threading.current_thread())
            self.events.append((kind, args))

        def on_loaded(self, *args): self._record("loaded", *args)
        def on_load_failed(self, *args): self._record("loadFailed", *args)
        def on_started(self, *args): self._record("started", *args)
        def on_progress(self, *args): self._record("progress", *args)
        def on_recognized(self, *args): self._record("recognized", *args)
        def on_failed(self, *args): self._record("failed", *args)

        def of(self, kind):
            return [args for k, args in self.events if k == kind]


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class EngineWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _start(self, **kwargs):
        engine = make_engine(**kwargs)
        collector = Collector(engine.signals)
        engine.start()
        self.addCleanup(engine.stop)
        return engine, collector

    def test_load_reports_backend_info(self):
        engine, got = self._start(info="检测 Dml · OCR cpu")
        self.assertTrue(wait_until(self.app, lambda: got.of("loaded")))
        self.assertEqual(got.of("loaded")[0], ("检测 Dml · OCR cpu",))

    def test_recognition_emits_started_progress_recognized_in_order(self):
        engine, got = self._start()
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("recognized")))
        kinds = [k for k, _ in got.events]
        self.assertLess(kinds.index("started"), kinds.index("progress"))
        self.assertLess(kinds.index("progress"), kinds.index("recognized"))
        page_id, data = got.of("recognized")[0]
        self.assertEqual(page_id, "001")
        self.assertEqual(len(data.blocks), 2)
        self.assertEqual(data.blocks[0].source, "ふはは")
        self.assertEqual([a[1:] for a in got.of("progress")], [("detect", 2, 3), ("ocr", 3, 3)])

    def test_signals_are_delivered_on_the_gui_thread(self):
        """The main window relies on this: results must not run on the worker thread."""
        engine, got = self._start()
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("recognized")))
        self.assertEqual(got.threads, {threading.main_thread()})

    def test_jobs_run_in_submit_order(self):
        engine, got = self._start()
        for page_id in ("001", "002", "003"):
            engine.submit(page_id, f"{page_id}.png")
        self.assertTrue(wait_until(self.app, lambda: len(got.of("recognized")) == 3))
        self.assertEqual([a[0] for a in got.of("recognized")], ["001", "002", "003"])

    def test_discarded_queued_page_is_never_recognized(self):
        gate = threading.Event()
        extractor = FakeExtractor(gate=gate)
        engine, got = self._start(extractor=extractor)
        engine.submit("001", "a.png")                       # starts and blocks on the gate
        self.assertTrue(wait_until(self.app, lambda: got.of("started")))
        engine.submit("002", "b.png")                       # waits in the queue
        engine.discard("002")
        self.assertEqual(engine.pending_count(), 0)
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: got.of("recognized")))
        time.sleep(0.2)
        self.app.processEvents()
        self.assertEqual([a[0] for a in got.of("recognized")], ["001"])
        self.assertEqual(extractor.images, ["a.png"])       # 002 never reached the extractor

    def test_discarding_a_running_page_suppresses_its_result(self):
        gate = threading.Event()
        engine, got = self._start(extractor=FakeExtractor(gate=gate))
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("started")))
        engine.discard("001")
        gate.set()
        time.sleep(0.3)
        self.app.processEvents()
        self.assertEqual(got.of("recognized"), [])
        self.assertEqual(got.of("failed"), [])

    def test_resubmitting_a_page_supersedes_older_queued_jobs(self):
        gate = threading.Event()
        extractor = FakeExtractor(gate=gate)
        engine, got = self._start(extractor=extractor)
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("started")))
        engine.submit("002", "old.png")
        engine.submit("002", "new.png")                     # same id again
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: len(got.of("recognized")) == 2))
        self.assertEqual(extractor.images, ["a.png", "new.png"])

    def test_an_extractor_error_fails_that_page_only(self):
        extractor = FakeExtractor(fail_for=lambda image: image == "bad.png")
        engine, got = self._start(extractor=extractor)
        engine.submit("001", "bad.png")
        engine.submit("002", "good.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("recognized")))
        self.assertEqual([a[0] for a in got.of("failed")], ["001"])
        self.assertIn("boom", got.of("failed")[0][1])
        self.assertEqual([a[0] for a in got.of("recognized")], ["002"])

    def test_unreadable_image_fails_the_page(self):
        engine = make_engine()
        engine._read_image = lambda path: None
        got = Collector(engine.signals)
        engine.start()
        engine.submit("001", "missing.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("failed")))
        self.assertIn("无法读取图片", got.of("failed")[0][1])

    def test_load_failure_is_reported_and_pages_fail_with_the_reason(self):
        engine, got = self._start(load_error="model missing")
        self.assertTrue(wait_until(self.app, lambda: got.of("loadFailed")))
        self.assertIn("model missing", got.of("loadFailed")[0][0])
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("failed")))
        self.assertIn("model missing", got.of("failed")[0][1])
        self.assertEqual(got.of("recognized"), [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class EngineReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def build(self):
        """An engine whose factory hands out a fresh FakeExtractor every time it is called."""
        from manga_extract.studio.workers import EngineWorker

        self.extractors, self.seen_old = [], []
        holder = {}

        def factory():
            # Record what the worker still holds when the new models are being built.
            self.seen_old.append(holder["engine"]._extractor)
            extractor = FakeExtractor()
            self.extractors.append(extractor)
            return extractor, f"load #{len(self.extractors)}"

        engine = EngineWorker(factory, image_reader=lambda path: path)
        holder["engine"] = engine
        got = Collector(engine.signals)
        engine.start()
        self.addCleanup(engine.stop)
        return engine, got

    def test_reload_builds_new_models_and_reports_again(self):
        engine, got = self.build()
        self.assertTrue(wait_until(self.app, lambda: got.of("loaded")))
        engine.reload()
        self.assertTrue(wait_until(self.app, lambda: len(got.of("loaded")) == 2))
        self.assertEqual([args[0] for args in got.of("loaded")], ["load #1", "load #2"])
        self.assertIsNot(engine._extractor, self.extractors[0])
        self.assertIs(engine._extractor, self.extractors[1])

    def test_the_old_models_are_released_before_the_new_ones_are_built(self):
        engine, got = self.build()
        self.assertTrue(wait_until(self.app, lambda: got.of("loaded")))
        engine.reload()
        self.assertTrue(wait_until(self.app, lambda: len(got.of("loaded")) == 2))
        self.assertEqual(self.seen_old, [None, None])                  # never two copies at once

    def test_pages_queued_around_a_reload_run_on_the_right_models(self):
        engine, got = self.build()
        engine.submit("001", "a.png")                                  # before the reload
        engine.reload()
        engine.submit("002", "b.png")                                  # after it
        self.assertTrue(wait_until(self.app, lambda: len(got.of("recognized")) == 2))
        self.assertEqual(self.extractors[0].images, ["a.png"])
        self.assertEqual(self.extractors[1].images, ["b.png"])

    def test_a_failed_reload_is_reported_and_later_pages_fail_with_the_reason(self):
        from manga_extract.studio.workers import EngineWorker

        state = {"fail": False}

        def factory():
            if state["fail"]:
                raise RuntimeError("bad device")
            return FakeExtractor(), "ok"

        engine = EngineWorker(factory, image_reader=lambda path: path)
        got = Collector(engine.signals)
        engine.start()
        self.addCleanup(engine.stop)
        self.assertTrue(wait_until(self.app, lambda: got.of("loaded")))
        state["fail"] = True
        engine.reload()
        engine.submit("001", "a.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("loadFailed") and got.of("failed")))
        self.assertIn("bad device", got.of("loadFailed")[0][0])
        self.assertIn("bad device", got.of("failed")[0][1])
        state["fail"] = False
        engine.reload()                                                 # and it can recover
        engine.submit("002", "b.png")
        self.assertTrue(wait_until(self.app, lambda: got.of("recognized")))
        self.assertEqual([a[0] for a in got.of("recognized")], ["002"])


if __name__ == "__main__":
    unittest.main()
