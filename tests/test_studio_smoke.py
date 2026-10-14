"""Offscreen smoke test of the studio window (no display needed).

Run:  python -m unittest discover -s tests -v
Skipped automatically when PySide6 / qfluentwidgets are not installed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtWidgets import QApplication

    import qfluentwidgets  # noqa: F401

    HAVE_QT = True
except Exception:  # pragma: no cover - environment without Qt
    HAVE_QT = False

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]


@unittest.skipUnless(HAVE_QT, "PySide6 / qfluentwidgets not installed")
@unittest.skipUnless(all(p.is_file() for p in SAMPLES), "sample images missing")
class StudioWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow

        from _studio_fakes import dispose_window, make_engine, tempdir

        self.tmp = tempdir(self)
        # Fake engine (no model is loaded); auto-recognition is covered in test_studio_flow.
        self.engine = make_engine()
        self.addCleanup(self.engine.stop)
        self.window = MainWindow(StudioConfig(auto_recognize=False), engine=self.engine)
        self.addCleanup(dispose_window, self.app, self.window)

    def _open_new_project(self):
        from manga_extract.studio.project import Project

        self.window.project = Project.create(self.tmp / "第1话")
        self.window._enter_project()
        return self.window.project

    def test_starts_on_welcome_page_with_import_disabled(self):
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertFalse(self.window.import_button.isEnabled())

    def test_import_adds_pages_and_selects_first(self):
        project = self._open_new_project()
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertTrue(self.window.import_button.isEnabled())
        self.window.import_paths([str(p) for p in SAMPLES])
        self.assertEqual(len(project.pages), 2)
        self.assertEqual(self.window.pages.list.count(), 2)
        self.assertEqual(self.window.pages.current_id(), project.pages[0].id)
        self.assertEqual(self.window.status_left.text(), "已导入 2 页")

    def test_import_without_project_is_refused(self):
        self.window.import_paths([str(SAMPLES[0])])
        self.assertIsNone(self.window.project)
        self.assertEqual(self.window.pages.list.count(), 0)

    def test_selecting_a_page_shows_it(self):
        project = self._open_new_project()
        self.window.import_paths([str(p) for p in SAMPLES])
        self.window.pages.select(project.pages[1].id)
        self.assertIn("2/2", self.window.status_left.text())
        self.assertFalse(self.window.canvas.view._item.pixmap().isNull())

    def test_reorder_is_persisted(self):
        from manga_extract.studio.project import Project

        project = self._open_new_project()
        self.window.import_paths([str(p) for p in SAMPLES])
        first, second = (p.id for p in project.pages)
        self.window._on_order_changed([second, first])
        self.assertEqual([p.id for p in Project.open(project.folder).pages], [second, first])

    def test_reorder_with_wrong_ids_does_not_corrupt_project(self):
        project = self._open_new_project()
        self.window.import_paths([str(p) for p in SAMPLES])
        before = [p.id for p in project.pages]
        self.window._on_order_changed(["999"])
        self.assertEqual([p.id for p in project.pages], before)

    def test_theme_switch_changes_background(self):
        from manga_extract.studio.ui_common import BG_DARK, BG_LIGHT

        self.window.set_theme("dark")
        self.assertIn(BG_DARK, self.window.styleSheet())
        self.window.set_theme("light")
        self.assertIn(BG_LIGHT, self.window.styleSheet())

    def test_reopening_a_project_restores_pages(self):
        project = self._open_new_project()
        self.window.import_paths([str(p) for p in SAMPLES])
        self.window.open_project(str(project.folder))
        self.assertEqual(self.window.pages.list.count(), 2)


@unittest.skipUnless(HAVE_QT, "PySide6 / qfluentwidgets not installed")
class ThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_thumbnail_is_not_oversized_on_high_dpi(self):
        """Regression: at dpr 2 the image was drawn twice as large and cropped."""
        from PySide6.QtGui import QColor, QImage

        from manga_extract.studio.pages_panel import THUMB_H, THUMB_W, _compose_thumbnail

        tall = QImage(100, 200, QImage.Format.Format_RGB32)  # 1:2 -> 48x96 in a 72x96 box
        tall.fill(QColor("#ff0000"))
        for dpr in (1.0, 2.0):
            canvas = _compose_thumbnail(tall, "pending", dpr).toImage()
            self.assertEqual(canvas.width(), int(THUMB_W * dpr))

            def alpha_at(x: float, y: float) -> int:
                # pixelColor keeps alpha; QColor(canvas.pixel(...)) would drop it.
                return canvas.pixelColor(int(x * dpr), int(y * dpr)).alpha()

            self.assertEqual(alpha_at(THUMB_W / 2, THUMB_H / 2), 255)   # inside the image
            self.assertEqual(alpha_at(66, 50), 0)                       # right of the 48px-wide image
            self.assertEqual(alpha_at(5, 50), 0)                        # left margin


if __name__ == "__main__":
    unittest.main()
