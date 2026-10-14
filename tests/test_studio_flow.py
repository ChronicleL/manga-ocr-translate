"""End-to-end behaviour of the studio window: recognition queue, races and the overlay.

The recogniser is a fake, so no model is loaded; the overlay uses the real
typeset code on the sample pages (skipped when no CJK font is installed).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import FakeExtractor, dispose_window, make_engine, tempdir, wait_until

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]
HAVE_SAMPLES = all(p.is_file() for p in SAMPLES)


def _font_available() -> bool:
    try:
        from manga_extract.typeset import find_cjk_font

        return find_cjk_font() is not None
    except Exception:
        return False


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class RecognitionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempdir(self)
        self.window = None

    def _window(self, auto: bool = True, extractor=None, load_error: str = "", overlay=None):
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow

        self.extractor = extractor or FakeExtractor()
        engine = make_engine(self.extractor, load_error=load_error)
        self.addCleanup(engine.stop)
        window = MainWindow(StudioConfig(auto_recognize=auto), engine=engine, overlay=overlay)
        self.addCleanup(dispose_window, self.app, window)
        self.addCleanup(window.overlay.shutdown)
        self.window = window
        return window

    def _open_project(self, name: str = "p"):
        from manga_extract.studio.project import Project

        self.window.project = Project.create(self.tmp / name)
        self.window._enter_project()
        return self.window.project

    def _all_recognized(self, project) -> bool:
        return all(project.load_page(p.id) is not None for p in project.pages)

    # -- the happy path -----------------------------------------------------
    def test_import_queues_pages_and_persists_results(self):
        window = self._window(auto=True)
        project = self._open_project()
        window.import_paths([str(p) for p in SAMPLES])
        self.assertTrue(wait_until(self.app, lambda: self._all_recognized(project)))
        first = project.pages[0].id
        self.assertEqual(len(project.load_page(first).blocks), 2)
        self.assertTrue(wait_until(self.app, lambda: window.pages._status[first] == "recognized"))
        self.assertEqual(window.canvas.view.block_count(), 2)          # shown on the canvas
        self.assertEqual(window._in_flight, set())

    def test_auto_recognize_off_leaves_pages_pending(self):
        window = self._window(auto=False)
        project = self._open_project()
        window.import_paths([str(p) for p in SAMPLES])
        self.app.processEvents()
        self.assertEqual(self.extractor.images, [])
        self.assertEqual({window.pages._status[p.id] for p in project.pages}, {"pending"})

    def test_recognize_pending_skips_pages_already_done(self):
        window = self._window(auto=False)
        project = self._open_project()
        window.import_paths([str(p) for p in SAMPLES])
        window.recognize_current()                                      # page 1 only
        self.assertTrue(wait_until(self.app, lambda: project.load_page(project.pages[0].id)))
        window.recognize_pending()                                      # must skip page 1
        self.assertTrue(wait_until(self.app, lambda: self._all_recognized(project)))
        self.assertEqual(len(self.extractor.images), 2)                 # each page read once

    def test_a_page_already_in_the_queue_is_not_queued_twice(self):
        gate = threading.Event()
        window = self._window(auto=False, extractor=FakeExtractor(gate=gate))
        project = self._open_project()
        window.import_paths([str(SAMPLES[0])])
        page_id = project.pages[0].id
        self.assertEqual(window.recognize_pages([page_id]), 1)
        self.assertEqual(window.recognize_pages([page_id]), 0)
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: self._all_recognized(project)))
        self.assertEqual(len(self.extractor.images), 1)

    # -- protecting the user's work ------------------------------------------
    def test_user_work_detection(self):
        from manga_extract.studio.main_window import _has_user_work
        from manga_extract.studio.project import BlockData, PageData

        self.assertFalse(_has_user_work(None))
        self.assertFalse(_has_user_work(PageData(1, 1, [BlockData(bbox=[0, 0, 1, 1], source="あ")])))
        for change in ({"translation": "啊"}, {"reviewed": True}, {"source_edited": True}):
            block = BlockData(bbox=[0, 0, 1, 1], source="あ", **change)
            self.assertTrue(_has_user_work(PageData(1, 1, [block])), change)

    def _recognized_page(self):
        window = self._window(auto=True)
        project = self._open_project()
        window.import_paths([str(SAMPLES[0])])
        page_id = project.pages[0].id
        self.assertTrue(wait_until(self.app, lambda: project.load_page(page_id) is not None))
        return window, project, page_id

    def test_rerecognizing_a_clean_page_does_not_prompt(self):
        window, _project, page_id = self._recognized_page()
        with mock.patch("manga_extract.studio.main_window.MessageBox") as box:
            self.assertTrue(window._confirm_overwrite(page_id))
        box.assert_not_called()

    def test_declining_the_overwrite_prompt_keeps_translations_and_queues_nothing(self):
        window, project, page_id = self._recognized_page()
        window._data[page_id].blocks[0].set_translation("哈哈")
        window.page_data_changed(page_id)
        calls = len(self.extractor.images)
        with mock.patch("manga_extract.studio.main_window.MessageBox") as box:
            box.return_value.exec.return_value = False
            window._on_reprocess_requested(page_id)
        self.app.processEvents()
        self.assertEqual(len(self.extractor.images), calls)
        self.assertEqual(project.load_page(page_id).blocks[0].translation, "哈哈")

    def test_accepting_the_overwrite_prompt_replaces_the_page_data(self):
        window, project, page_id = self._recognized_page()
        window._data[page_id].blocks[0].set_translation("哈哈")
        window.page_data_changed(page_id)
        with mock.patch("manga_extract.studio.main_window.MessageBox") as box:
            box.return_value.exec.return_value = True
            window._on_reprocess_requested(page_id)
        self.assertTrue(wait_until(
            self.app, lambda: project.load_page(page_id).blocks[0].translation == ""))
        self.assertEqual(window.pages._status[page_id], "recognized")

    # -- races ----------------------------------------------------------------
    def test_deleting_a_queued_page_applies_no_result(self):
        gate = threading.Event()
        window = self._window(auto=True, extractor=FakeExtractor(gate=gate))
        project = self._open_project()
        window.import_paths([str(p) for p in SAMPLES])
        first, second = (p.id for p in project.pages)
        self.assertTrue(wait_until(self.app, lambda: window.pages._status[first] == "busy"))
        with mock.patch("manga_extract.studio.main_window.MessageBox") as box:
            box.return_value.exec.return_value = True
            window._on_remove_requested(second)
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: project.load_page(first) is not None))
        self.app.processEvents()
        self.assertEqual([p.id for p in project.pages], [first])
        self.assertFalse(project.data_path(second).exists())
        self.assertEqual(len(self.extractor.images), 1)                # page 2 was never read

    def test_a_late_result_from_the_previous_project_is_dropped(self):
        gate = threading.Event()
        window = self._window(auto=True, extractor=FakeExtractor(gate=gate))
        project_a = self._open_project("a")
        window.import_paths([str(SAMPLES[0])])
        self.assertTrue(wait_until(self.app, lambda: window.pages._status["001"] == "busy"))
        project_b = self._open_project("b")                             # page ids repeat: "001"
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: len(self.extractor.images) >= 1))
        for _ in range(20):
            self.app.processEvents()
        self.assertFalse(project_a.data_path("001").exists())
        self.assertEqual(project_b.pages, [])
        self.assertEqual(window._in_flight, set())

    def test_engine_failure_restores_pending_and_reports_once(self):
        window = self._window(auto=True, load_error="model missing")
        project = self._open_project()
        window.import_paths([str(p) for p in SAMPLES])
        self.assertTrue(wait_until(self.app, lambda: window._in_flight == set()))
        self.assertEqual({window.pages._status[p.id] for p in project.pages}, {"pending"})
        self.assertEqual(window.status_right.text(), "识别引擎不可用")
        self.assertTrue(wait_until(self.app, lambda: window._failures == [], timeout=2))
        self.assertFalse(any(project.data_path(p.id).exists() for p in project.pages))

    def test_last_project_is_reopened_only_when_nothing_is_open(self):
        """Regression: the start-up 'reopen last project' used to wipe an already-open one."""
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.project import Project

        last = Project.create(self.tmp / "last")
        last.add_image_files([SAMPLES[0]])
        other = Project.create(self.tmp / "other")

        def build(open_first):
            engine = make_engine()
            self.addCleanup(engine.stop)
            window = MainWindow(StudioConfig(auto_recognize=False,
                                             recent_projects=[str(last.folder)]), engine=engine)
            self.addCleanup(dispose_window, self.app, window)
            self.addCleanup(window.overlay.shutdown)
            if open_first:
                window.project = Project.open(other.folder)
                window._enter_project()
            # Count re-entries: comparing folders cannot tell, because opening a project moves
            # it to the front of the recent list, so a wrongful reopen lands on the same one.
            entered = []
            original = window._enter_project
            window._enter_project = lambda: (entered.append(1), original())[1]
            for _ in range(10):
                self.app.processEvents()
            return window, len(entered)

        window, entered = build(open_first=False)
        self.assertEqual((window.project.folder, entered), (last.folder, 1))   # restored once
        window, entered = build(open_first=True)
        self.assertEqual((window.project.folder, entered), (other.folder, 0))  # left alone

    def test_engine_ready_updates_the_status_bar(self):
        window = self._window(auto=False)
        self.assertTrue(wait_until(self.app, lambda: window._engine_ready))
        self.assertEqual(window.status_right.text(), "检测 Fake · OCR cpu")
        self.assertTrue(window.engine_bar.isHidden())

    def test_clicking_a_block_describes_it_in_the_status_bar(self):
        window, _project, _page_id = self._recognized_page()
        window._on_block_clicked(0)
        self.assertEqual(window.canvas.view.selected_block, 0)
        self.assertIn("文本块 #1", window.status_left.text())
        self.assertIn("ふはは", window.status_left.text())


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES and _font_available(),
                     "PySide6, sample images or a CJK font missing")
class OverlayFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.project import Project

        self.tmp = tempdir(self)
        self.extractor = FakeExtractor()
        engine = make_engine(self.extractor)
        self.addCleanup(engine.stop)
        self.window = MainWindow(StudioConfig(auto_recognize=True), engine=engine)
        self.addCleanup(dispose_window, self.app, self.window)
        self.addCleanup(self.window.overlay.shutdown)
        self.project = Project.create(self.tmp / "p")
        self.window.project = self.project
        self.window._enter_project()
        self.window.import_paths([str(p) for p in SAMPLES])
        self.first, self.second = (p.id for p in self.project.pages)
        self.assertTrue(wait_until(self.app, lambda: all(
            self.project.load_page(p.id) is not None for p in self.project.pages)))

    def _translate(self, page_id: str, text: str = "哈哈哈") -> None:
        for block in self.window._data[page_id].blocks:
            block.set_translation(text)
        self.window.page_data_changed(page_id)

    @property
    def bar(self):
        return self.window.canvas.bar

    @property
    def view(self):
        return self.window.canvas.view

    def _overlay_ready(self) -> bool:
        return self.bar.overlay_button.isEnabled()

    def test_overlay_is_unavailable_until_the_page_has_a_translation(self):
        self.assertFalse(self.bar.overlay_button.isEnabled())
        self.assertFalse(self.view.has_overlay())

    def test_translating_enables_the_switch_and_it_flips_the_canvas(self):
        self._translate(self.first)
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        self.assertFalse(self.view.is_showing_overlay())                 # starts on the original
        self.bar.overlay_button.click()
        self.assertTrue(self.view.is_showing_overlay())
        self.assertNotEqual(self.view._original.toImage(), self.view._overlay.toImage())
        self.assertEqual(self.view._overlay.size(), self.view._original.size())
        self.bar.overlay_button.click()
        self.assertFalse(self.view.is_showing_overlay())

    def test_choice_of_original_or_overlay_carries_over_to_the_next_page(self):
        self._translate(self.first)
        self._translate(self.second)
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        self.bar.overlay_button.click()                                   # choose "overlay"
        self.window.pages.select(self.second)
        self.assertTrue(wait_until(self.app, lambda: self._overlay_ready()
                                   and self.view.is_showing_overlay(), timeout=15))
        self.window.pages.select(self.first)
        self.assertTrue(wait_until(self.app, lambda: self.view.is_showing_overlay(), timeout=15))

    def test_editing_a_translation_regenerates_the_overlay(self):
        self._translate(self.first, "哈哈哈")
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        before = self.view._overlay.toImage()
        self._translate(self.first, "完全不同的另一句译文")
        self.assertFalse(self.bar.overlay_button.isEnabled())            # busy re-rendering
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        self.assertNotEqual(self.view._overlay.toImage(), before)

    def test_removing_all_translations_disables_the_switch_again(self):
        self._translate(self.first)
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        self.bar.overlay_button.click()
        self._translate(self.first, "")
        self.assertFalse(self.bar.overlay_button.isEnabled())
        self.assertFalse(self.view.is_showing_overlay())

    def test_a_stale_overlay_result_is_ignored(self):
        from PySide6.QtGui import QColor, QImage

        self._translate(self.first)
        self.assertTrue(wait_until(self.app, self._overlay_ready, timeout=15))
        current = self.window._versions[self.first]
        cached_before = self.window._overlay_cache[self.first]
        stale = QImage(10, 10, QImage.Format.Format_RGB888)
        stale.fill(QColor("#ff00ff"))
        self.window._on_overlay_rendered(self.first, current - 1, stale)
        self.assertIs(self.window._overlay_cache[self.first], cached_before)

    def test_overlay_cache_is_bounded(self):
        from manga_extract.studio.main_window import OVERLAY_CACHE_PAGES
        from PySide6.QtGui import QImage

        for index in range(OVERLAY_CACHE_PAGES + 3):
            page_id = f"x{index}"
            self.window._versions[page_id] = 1
            self.window._on_overlay_rendered(page_id, 1, QImage(4, 4, QImage.Format.Format_RGB888))
        self.assertLessEqual(len(self.window._overlay_cache), OVERLAY_CACHE_PAGES)


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES and _font_available(),
                     "PySide6, sample images or a CJK font missing")
class OverlayRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.overlay import OverlayRenderer
        from manga_extract.studio.project import BlockData, PageData

        self.renderer = OverlayRenderer()
        self.addCleanup(self.renderer.shutdown)
        self.data = PageData(713, 861, [
            BlockData(bbox=[500, 50, 560, 300], vertical=True, source="ふはは", translation="哈哈哈"),
            BlockData(bbox=[100, 600, 300, 680], source="こんにちは", translation="你好"),
        ])

        class Sink(QObject):
            def __init__(self):
                super().__init__()
                self.done, self.errors = [], []

            def on_rendered(self, page_id, version, image):
                self.done.append((page_id, version, image))

            def on_failed(self, page_id, version, message):
                self.errors.append((page_id, version, message))

        self.sink = Sink()
        self.renderer.rendered.connect(self.sink.on_rendered)
        self.renderer.failed.connect(self.sink.on_failed)

    def test_renders_an_image_the_size_of_the_page_with_text_drawn(self):
        from PySide6.QtGui import QImage

        self.renderer.request("001", 1, SAMPLES[0], self.data)
        self.assertTrue(wait_until(self.app, lambda: self.sink.done, timeout=15))
        page_id, version, image = self.sink.done[0]
        original = QImage(str(SAMPLES[0]))
        self.assertEqual((page_id, version), ("001", 1))
        self.assertEqual(image.size(), original.size())
        self.assertNotEqual(image.convertToFormat(original.format()), original)

    def test_only_the_newest_version_of_a_page_is_rendered(self):
        gate = threading.Event()
        self.renderer._executor.submit(gate.wait, 10)       # occupy the single worker
        self.renderer.request("001", 1, SAMPLES[0], self.data)
        self.renderer.request("001", 2, SAMPLES[0], self.data)
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: self.sink.done, timeout=15))
        for _ in range(20):
            self.app.processEvents()
        self.assertEqual([v for _, v, _ in self.sink.done], [2])

    def test_forgetting_a_page_skips_its_pending_render(self):
        gate = threading.Event()
        self.renderer._executor.submit(gate.wait, 10)
        self.renderer.request("001", 1, SAMPLES[0], self.data)
        self.renderer.forget("001")
        gate.set()
        for _ in range(40):
            self.app.processEvents()
        self.assertEqual(self.sink.done, [])

    def test_a_missing_image_reports_failure(self):
        missing = Path(tempfile.gettempdir()) / "studio_test_definitely_missing.png"
        self.renderer.request("001", 1, missing, self.data)
        self.assertTrue(wait_until(self.app, lambda: self.sink.errors, timeout=15))
        self.assertEqual(self.sink.errors[0][:2], ("001", 1))
        self.assertEqual(self.sink.done, [])

    def test_translation_detection(self):
        from manga_extract.studio.overlay import has_translation
        from manga_extract.studio.project import BlockData, PageData

        self.assertFalse(has_translation(None))
        self.assertFalse(has_translation(PageData(1, 1, [BlockData(bbox=[0, 0, 1, 1], source="あ")])))
        self.assertTrue(has_translation(self.data))


if __name__ == "__main__":
    unittest.main()
