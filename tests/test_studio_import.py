"""Importing through the main window: clipboard, screenshot and web (offscreen)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QMimeData, QObject, QPoint, QRect, QUrl, Qt, Signal
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import (
        FakeExtractor,
        dispose_window,
        make_engine,
        tempdir,
        wait_until,
    )
    from test_studio_capture import FakeScreen
    from test_studio_web import SiteMixin
    from test_web_import import png

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]
HAVE_SAMPLES = all(p.is_file() for p in SAMPLES)


if HAVE_QT:
    class FakeCapture(QObject):
        """Stands in for ScreenCapture: the test decides when and how it finishes."""

        captured = Signal(QImage)
        cancelled = Signal()

        def __init__(self):
            super().__init__()
            self.active, self.starts = False, 0

        def start(self):
            self.active = True
            self.starts += 1

    class FakeDialog:
        """Stands in for WebImportDialog (the real one is modal and blocks the test)."""

        def __init__(self, images=(), accept=True, url="", skipped=0):
            self.images, self.accept, self.url, self.skipped = list(images), accept, url, skipped
            self.opened_with = None

        def exec(self):
            return self.accept

        def current_url(self):
            return self.url


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class ImportHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempdir(self)
        QApplication.clipboard().clear()
        self.addCleanup(QApplication.clipboard().clear)

    def make_window(self, project: bool = True, auto: bool = True, **kwargs):
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.project import Project
        from manga_extract.translation import TranslationConfig

        self.extractor = FakeExtractor()
        engine = make_engine(self.extractor)
        self.addCleanup(engine.stop)
        self.config = StudioConfig(auto_recognize=auto)
        window = MainWindow(self.config, engine=engine, translation_config=TranslationConfig(),
                            translate_config_path=self.tmp / "t.json", **kwargs)
        self.addCleanup(dispose_window, self.app, window)
        self.addCleanup(window.overlay.shutdown)
        self.addCleanup(window.translator.stop)
        self.assertTrue(wait_until(self.app, lambda: window._engine_ready))
        self.window = window
        if project:
            self.project = Project.create(self.tmp / "p")
            window.project = self.project
            window._enter_project()
        return window

    def origins(self):
        return [p.origin for p in self.project.pages]

    def page_image(self, index: int = 0):
        return QImage(str(self.project.image_path(self.project.pages[index].id)))

    def set_clipboard_image(self, width=40, height=30, color="#ff8800"):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        QApplication.clipboard().setImage(image)
        return image


class ClipboardImportTests(ImportHarness):
    def test_a_clipboard_image_becomes_a_page(self):
        window = self.make_window(auto=False)
        self.set_clipboard_image(40, 30, "#ff8800")
        window.import_from_clipboard()
        self.assertEqual(self.origins(), ["剪贴板"])
        image = self.page_image()
        self.assertEqual((image.width(), image.height()), (40, 30))
        self.assertEqual(image.pixelColor(5, 5).name(), "#ff8800")
        self.assertEqual(window.pages.list.count(), 1)
        self.assertEqual(window.pages.current_id(), self.project.pages[0].id)
        self.assertTrue(self.project.image_path(self.project.pages[0].id).suffix == ".png")

    def test_it_is_queued_for_recognition_like_any_import(self):
        window = self.make_window(auto=True)
        self.set_clipboard_image()
        window.import_from_clipboard()
        page_id = self.project.pages[0].id
        self.assertTrue(wait_until(self.app, lambda: self.project.load_page(page_id) is not None))

    def test_copied_image_files_are_imported_in_order(self):
        window = self.make_window(auto=False)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in SAMPLES])
        QApplication.clipboard().setMimeData(mime)
        window.import_from_clipboard()
        self.assertEqual(self.origins(), ["1.png", "2.png"])

    def test_an_image_wins_over_file_urls(self):
        window = self.make_window(auto=False)
        mime = QMimeData()
        mime.setImageData(QImage(20, 20, QImage.Format.Format_RGB32))
        mime.setUrls([QUrl.fromLocalFile(str(SAMPLES[0]))])
        QApplication.clipboard().setMimeData(mime)
        window.import_from_clipboard()
        self.assertEqual(self.origins(), ["剪贴板"])

    def test_nothing_usable_in_the_clipboard_adds_nothing(self):
        window = self.make_window(auto=False)
        for prepare in (lambda: QApplication.clipboard().setText("just text"),
                        lambda: QApplication.clipboard().clear(),
                        lambda: self._set_urls(["https://example.com/a.png"])):
            prepare()
            window.import_from_clipboard()
        self.assertEqual(self.project.pages, [])
        self.assertEqual(window.pages.list.count(), 0)

    def _set_urls(self, urls):
        mime = QMimeData()
        mime.setUrls([QUrl(u) for u in urls])
        QApplication.clipboard().setMimeData(mime)

    def test_without_a_project_nothing_happens(self):
        window = self.make_window(project=False)
        self.set_clipboard_image()
        window.import_from_clipboard()
        self.assertIsNone(window.project)

    def test_the_import_menu_and_buttons_follow_the_project_state(self):
        window = self.make_window(project=False)
        self.assertFalse(window.act_capture.isEnabled())
        self.assertFalse(window.act_web.isEnabled())
        self.assertFalse(window.import_button.isEnabled())
        from manga_extract.studio.project import Project

        window.project = Project.create(self.tmp / "p")
        window._enter_project()
        self.assertTrue(window.act_capture.isEnabled())
        self.assertTrue(window.act_web.isEnabled())

    # -- Ctrl+V -----------------------------------------------------------
    def _show(self, window):
        window.resize(1280, 800)
        window.show()
        window.activateWindow()
        self.addCleanup(window.hide)
        for _ in range(10):
            self.app.processEvents()

    def test_ctrl_v_on_the_page_list_imports_the_clipboard_image(self):
        window = self.make_window(auto=False)
        self._show(window)
        self.set_clipboard_image()
        window.pages.list.setFocus()
        QTest.keyClick(window.pages.list, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.origins(), ["剪贴板"])

    def test_ctrl_v_in_a_text_box_pastes_text_and_does_not_import(self):
        window = self.make_window(auto=True)
        window.import_paths([str(SAMPLES[0])])
        self.assertTrue(wait_until(self.app, lambda: window.blocks_panel.card_count() == 2))
        self._show(window)
        mime = QMimeData()
        mime.setText("粘贴进去的文字")
        mime.setImageData(QImage(20, 20, QImage.Format.Format_RGB32))       # both are on the clipboard
        QApplication.clipboard().setMimeData(mime)
        edit = window.blocks_panel.card(0).target_edit
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.project.pages), 1)                          # no new page
        self.assertEqual(edit.toPlainText(), "粘贴进去的文字")                  # the text went to the box


class ScreenshotImportTests(ImportHarness):
    def make_with_fake_capture(self, **kwargs):
        self.fake = FakeCapture()
        window = self.make_window(capture_factory=lambda w, accent: self.fake, **kwargs)
        return window

    def test_a_screenshot_becomes_a_page(self):
        window = self.make_with_fake_capture(auto=False)
        window.capture_screen()
        self.assertEqual(self.fake.starts, 1)
        image = QImage(60, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        self.fake.captured.emit(image)
        self.assertEqual(self.origins(), ["截图"])
        shot = self.page_image()
        self.assertEqual((shot.width(), shot.height()), (60, 40))
        self.assertEqual(shot.pixelColor(3, 3).name(), "#336699")

    def test_cancelling_adds_nothing_and_says_so(self):
        window = self.make_with_fake_capture(auto=False)
        window.capture_screen()
        self.fake.cancelled.emit()
        self.assertEqual(self.project.pages, [])
        self.assertIn("已取消", window.status_left.text())

    def test_a_second_request_while_capturing_is_ignored(self):
        window = self.make_with_fake_capture(auto=False)
        window.capture_screen()
        window.capture_screen()
        self.assertEqual(self.fake.starts, 1)

    def test_it_can_capture_again_after_finishing(self):
        window = self.make_with_fake_capture(auto=False)
        for _ in range(2):
            window.capture_screen()
            self.fake.active = False
            self.fake.captured.emit(QImage(30, 30, QImage.Format.Format_RGB32))
        self.assertEqual(self.fake.starts, 2)
        self.assertEqual(self.origins(), ["截图", "截图"])

    def test_without_a_project_it_does_not_start(self):
        window = self.make_with_fake_capture(project=False)
        window.capture_screen()
        self.assertEqual(self.fake.starts, 0)

    def test_a_null_screenshot_is_refused(self):
        window = self.make_with_fake_capture(auto=False)
        window.capture_screen()
        self.fake.captured.emit(QImage())
        self.assertEqual(self.project.pages, [])

    def test_the_real_controller_end_to_end_with_fake_screens(self):
        """Window hides, overlays appear, a drag selects, a page is added, the window returns."""
        from manga_extract.studio.capture_qt import ScreenCapture
        from test_studio_capture import coordinate_pixmap

        screens = [FakeScreen(QRect(0, 0, 400, 300), 2.0)]
        factory = lambda w, accent: ScreenCapture(w, accent, grabber=lambda s: s.pixmap(),  # noqa: E731
                                                  screens=lambda: screens, delay_ms=10)
        window = self.make_window(auto=False, capture_factory=factory)
        window.show()
        self.addCleanup(window.hide)
        self.app.processEvents()
        window.capture_screen()
        controller = window._capture
        self.assertTrue(wait_until(self.app, lambda: controller._overlays))
        self.assertFalse(window.isVisible())
        overlay = controller._overlays[0]
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        QTest.mouseMove(overlay, QPoint(60, 50))
        QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 50))
        self.assertTrue(window.isVisible())
        shot = self.page_image()
        self.assertEqual((shot.width(), shot.height()), (100, 80))               # 2x screen, physical px
        self.assertEqual(shot.pixelColor(0, 0).red(), 20)


class WebImportTests(ImportHarness):
    def images(self, n=3):
        from manga_extract.studio.web_import import DownloadedImage

        return [DownloadedImage(f"https://site.example/img/{i}.png", png(30 + i, 40, "#aa0000"), ".png")
                for i in range(n)]

    def test_chosen_images_become_pages_in_order_with_their_url_as_origin(self):
        dialog = FakeDialog(self.images(3), url="https://site.example/ch/1")
        window = self.make_window(auto=False, web_dialog_factory=lambda parent, url: dialog)
        window.import_from_web()
        self.assertEqual(self.origins(), [f"https://site.example/img/{i}.png" for i in range(3)])
        self.assertEqual([self.page_image(i).width() for i in range(3)], [30, 31, 32])
        self.assertEqual(window.pages.list.count(), 3)

    def test_the_last_url_is_remembered_and_offered_next_time(self):
        seen = []
        dialog = FakeDialog(self.images(1), url="https://site.example/ch/7")
        window = self.make_window(auto=False,
                                  web_dialog_factory=lambda parent, url: (seen.append(url), dialog)[1])
        window.import_from_web()
        window.import_from_web()
        self.assertEqual(seen, ["", "https://site.example/ch/7"])

    def test_a_cancelled_dialog_adds_nothing_but_still_remembers_the_url(self):
        dialog = FakeDialog(self.images(2), accept=False, url="https://site.example/ch/1")
        window = self.make_window(auto=False, web_dialog_factory=lambda parent, url: dialog)
        window.import_from_web()
        self.assertEqual(self.project.pages, [])
        self.assertEqual(self.config.last_url, "https://site.example/ch/1")

    def test_accepting_with_nothing_selected_adds_nothing(self):
        dialog = FakeDialog([], accept=True, url="https://site.example/ch/1")
        window = self.make_window(auto=False, web_dialog_factory=lambda parent, url: dialog)
        window.import_from_web()
        self.assertEqual(self.project.pages, [])

    def test_skipped_files_do_not_break_the_import(self):
        dialog = FakeDialog(self.images(2), skipped=1)
        window = self.make_window(auto=False, web_dialog_factory=lambda parent, url: dialog)
        window.import_from_web()
        self.assertEqual(len(self.project.pages), 2)

    def test_without_a_project_the_dialog_is_not_even_opened(self):
        opened = []
        window = self.make_window(project=False,
                                  web_dialog_factory=lambda parent, url: (opened.append(1), FakeDialog())[1])
        window.import_from_web()
        self.assertEqual(opened, [])

    def test_web_pages_are_queued_for_recognition(self):
        dialog = FakeDialog(self.images(2))
        window = self.make_window(auto=True, web_dialog_factory=lambda parent, url: dialog)
        window.import_from_web()
        self.assertTrue(wait_until(self.app, lambda: all(
            self.project.load_page(p.id) is not None for p in self.project.pages)))

    def test_the_last_url_survives_in_the_saved_config(self):
        from manga_extract.studio.config import StudioConfig

        config = StudioConfig(last_url="https://site.example/ch/9")
        path = self.tmp / "studio.json"
        config.save(path)
        self.assertEqual(StudioConfig.load(path).last_url, "https://site.example/ch/9")
        self.assertEqual(StudioConfig.from_dict({"last_url": 5}).last_url, "")     # junk is ignored


class WebImportEndToEndTests(SiteMixin, ImportHarness):
    """Real dialog + real downloads from the local fake site + the recognition queue."""

    def test_from_a_url_to_recognised_pages(self):
        from manga_extract.studio.web_dialog import WebImportDialog

        self.start_site()
        test = self

        class AutoDialog(WebImportDialog):
            def exec(self):                                      # drive it instead of blocking
                self.show()
                self.url_edit.setText(test.page_url)
                self.parse_button.click()
                assert wait_until(test.app, lambda: len(self._cards) == 5
                                  and all(c.state != "loading" for c in self._cards))
                self.import_button.click()
                return True

        window = self.make_window(auto=True,
                                  web_dialog_factory=lambda parent, url: AutoDialog(parent, initial_url=url))
        window.import_from_web()
        # 3 full pages are ticked; the small icon and the blocked "image" are not.
        self.assertEqual(self.origins(), [f"{self.site.base}/img/{i}.png" for i in range(3)])
        self.assertEqual([self.page_image(i).width() for i in range(3)], [800, 800, 900])
        self.assertEqual(self.config.last_url, self.page_url)
        self.assertTrue(wait_until(self.app, lambda: all(
            self.project.load_page(p.id) is not None for p in self.project.pages)))
        self.assertEqual(len(self.project.load_page(self.project.pages[0].id).blocks), 2)


if __name__ == "__main__":
    unittest.main()
