"""CanvasView / CanvasPane / FloatingBar behaviour (offscreen)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPixmap, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(ROOT / "tests"))
    from _studio_fakes import dispose_widget

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


def solid(width: int, height: int, color: str) -> "QPixmap":
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return QPixmap.fromImage(image)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CanvasViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.canvas import CanvasView
        from manga_extract.studio.project import BlockData

        self.view = CanvasView()
        self.view.resize(500, 500)
        self.view.show()
        self.addCleanup(dispose_widget, self.app, self.view)
        self.blocks = [BlockData(bbox=[50, 50, 150, 200]), BlockData(bbox=[250, 300, 350, 400])]
        self.view.set_page(solid(400, 600, "#808080"), self.blocks)
        self.app.processEvents()
        self.clicks = []
        self.view.blockClicked.connect(self.clicks.append)

    def _viewport_point(self, x: float, y: float) -> "QPoint":
        return self.view.mapFromScene(QPointF(x, y))

    # -- zoom ---------------------------------------------------------------
    def test_page_is_fitted_into_the_view(self):
        self.assertAlmostEqual(self.view.scale_factor(), 500 / 600, delta=0.02)
        self.assertTrue(self.view._fit_mode)

    def test_small_pages_are_not_blown_up_past_100_percent(self):
        self.view.set_page(solid(100, 100, "#808080"), [])
        self.app.processEvents()
        self.assertLessEqual(self.view.scale_factor(), 1.0)

    def test_zoom_is_clamped(self):
        from manga_extract.studio.canvas import MAX_SCALE, MIN_SCALE

        self.view.zoom_by(1000)
        self.assertAlmostEqual(self.view.scale_factor(), MAX_SCALE, places=4)
        self.view.zoom_by(1e-6)
        self.assertAlmostEqual(self.view.scale_factor(), MIN_SCALE, places=4)

    def test_ctrl_wheel_zooms_and_plain_wheel_does_not(self):
        def wheel(modifiers):
            event = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, 120),
                                Qt.MouseButton.NoButton, modifiers,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(self.view.viewport(), event)

        before = self.view.scale_factor()
        wheel(Qt.KeyboardModifier.NoModifier)
        self.assertAlmostEqual(self.view.scale_factor(), before, places=6)
        wheel(Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(self.view.scale_factor(), before)
        self.assertFalse(self.view._fit_mode)

    def test_resize_refits_only_while_in_fit_mode(self):
        before = self.view.scale_factor()
        self.view.resize(300, 300)
        self.app.processEvents()
        self.assertLess(self.view.scale_factor(), before)         # still fitting

        self.view.zoom_by(2.0)
        zoomed = self.view.scale_factor()
        self.view.resize(450, 450)
        self.app.processEvents()
        self.assertAlmostEqual(self.view.scale_factor(), zoomed, places=6)  # user zoom is kept

    def test_double_click_fits_again(self):
        self.view.zoom_by(3.0)
        QTest.mouseDClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        self.assertTrue(self.view._fit_mode)
        self.assertAlmostEqual(self.view.scale_factor(), 500 / 600, delta=0.02)

    def test_zoom_changed_signal_reports_the_scale(self):
        seen = []
        self.view.zoomChanged.connect(seen.append)
        self.view.zoom_by(1.5)
        self.assertAlmostEqual(seen[-1], self.view.scale_factor(), places=6)

    # -- selection ----------------------------------------------------------
    def test_clicking_a_block_reports_its_index(self):
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, self._viewport_point(300, 350))
        self.assertEqual(self.clicks, [1])

    def test_clicking_empty_space_reports_minus_one(self):
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, self._viewport_point(200, 500))
        self.assertEqual(self.clicks, [-1])

    def test_dragging_pans_and_is_not_a_click(self):
        start, end = self._viewport_point(300, 350), self._viewport_point(300, 350) + QPoint(60, 40)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(self.view.viewport(), end)
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, end)
        self.assertEqual(self.clicks, [])

    def test_hidden_boxes_cannot_be_clicked(self):
        self.view.set_boxes_visible(False)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, self._viewport_point(300, 350))
        self.assertEqual(self.clicks, [-1])
        self.assertFalse(any(item.isVisible() for item in self.view._blocks))

    def test_select_block_highlights_and_rejects_bad_index(self):
        self.view.select_block(1)
        self.assertEqual(self.view.selected_block, 1)
        self.assertTrue(self.view._blocks[1]._selected)
        self.view.select_block(0)
        self.assertFalse(self.view._blocks[1]._selected)       # the old one is cleared
        self.view.select_block(9)
        self.assertEqual(self.view.selected_block, -1)

    def test_selection_survives_set_blocks_when_still_valid(self):
        self.view.select_block(1)
        self.view.set_blocks(self.blocks)
        self.assertEqual(self.view.selected_block, 1)
        self.assertTrue(self.view._blocks[1]._selected)
        self.view.set_blocks(self.blocks[:1])                   # block 1 no longer exists
        self.assertEqual(self.view.selected_block, -1)

    # -- original <-> overlay -----------------------------------------------
    def _shown_color(self) -> str:
        return self.view._item.pixmap().toImage().pixelColor(10, 10).name()

    def test_overlay_swaps_the_shown_pixmap(self):
        self.assertFalse(self.view.has_overlay())
        self.view.set_overlay(solid(400, 600, "#ff0000"))
        self.assertTrue(self.view.has_overlay())
        self.assertEqual(self._shown_color(), "#808080")        # still the original
        self.view.show_overlay(True)
        self.assertEqual(self._shown_color(), "#ff0000")
        self.assertTrue(self.view.is_showing_overlay())
        self.view.show_overlay(False)
        self.assertEqual(self._shown_color(), "#808080")

    def test_showing_overlay_without_one_falls_back_to_original(self):
        self.view.show_overlay(True)
        self.assertEqual(self._shown_color(), "#808080")
        self.assertFalse(self.view.is_showing_overlay())

    def test_clear_overlay_returns_to_original_even_when_shown(self):
        self.view.set_overlay(solid(400, 600, "#ff0000"))
        self.view.show_overlay(True)
        self.view.clear_overlay()
        self.assertEqual(self._shown_color(), "#808080")

    def test_new_page_drops_the_previous_overlay(self):
        self.view.set_overlay(solid(400, 600, "#ff0000"))
        self.view.show_overlay(True)
        self.view.set_page(solid(400, 600, "#00ff00"), [])
        self.assertFalse(self.view.has_overlay())
        self.assertEqual(self._shown_color(), "#00ff00")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CanvasPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.canvas import CanvasPane

        self.pane = CanvasPane()
        self.pane.resize(600, 500)
        self.pane.show()
        self.addCleanup(dispose_widget, self.app, self.pane)
        self.pane.show_page(solid(400, 600, "#808080"), [])
        self.app.processEvents()

    def test_bar_is_centred_near_the_bottom(self):
        bar = self.pane.bar
        self.assertAlmostEqual(bar.x() + bar.width() / 2, self.pane.width() / 2, delta=2)
        self.assertGreater(bar.y(), self.pane.height() / 2)
        self.assertLessEqual(bar.y() + bar.height(), self.pane.height())

    def test_bar_stays_centred_after_resize(self):
        self.pane.resize(900, 700)
        self.app.processEvents()
        bar = self.pane.bar
        self.assertAlmostEqual(bar.x() + bar.width() / 2, 450, delta=2)

    def test_message_replaces_the_page_and_hides_the_bar(self):
        self.pane.show_message("导入页面后在这里查看")
        self.assertTrue(self.pane.hint.isVisible())
        self.assertEqual(self.pane.hint.text(), "导入页面后在这里查看")
        self.assertFalse(self.pane.bar.isVisible())
        self.pane.show_page(solid(10, 10, "#808080"), [])
        self.assertFalse(self.pane.hint.isVisible())
        self.assertTrue(self.pane.bar.isVisible())

    def test_overlay_button_starts_disabled_and_only_user_toggles_emit(self):
        bar = self.pane.bar
        emitted = []
        self.pane.overlayToggled.connect(emitted.append)
        self.assertFalse(bar.overlay_button.isEnabled())
        bar.toggle_overlay()                                   # disabled: nothing happens
        self.assertEqual(emitted, [])
        bar.set_overlay_state(True, "tip", checked=True)       # programmatic: silent
        self.assertTrue(bar.overlay_checked())
        self.assertEqual(emitted, [])
        bar.toggle_overlay()
        self.assertEqual(emitted, [False])

    def test_disabling_the_overlay_button_unchecks_it(self):
        bar = self.pane.bar
        bar.set_overlay_state(True, "tip", checked=True)
        bar.set_overlay_state(False, "tip", checked=True)
        self.assertFalse(bar.overlay_checked())

    def test_zoom_label_follows_the_view(self):
        self.pane.view.zoom_by(2.0)
        self.assertEqual(self.pane.bar.zoom_label.text(),
                         f"{round(self.pane.view.scale_factor() * 100)}%")

    def test_boxes_button_toggles_view_outlines(self):
        from manga_extract.studio.project import BlockData

        self.pane.view.set_blocks([BlockData(bbox=[1, 1, 20, 20])])
        self.pane.bar.boxes_button.setChecked(False)
        self.assertFalse(self.pane.view._blocks[0].isVisible())
        self.pane.bar.boxes_button.setChecked(True)
        self.assertTrue(self.pane.view._blocks[0].isVisible())

    def test_space_flips_between_original_and_overlay(self):
        self.pane.bar.set_overlay_state(True, "tip", checked=False)
        self.pane.activateWindow()
        self.pane.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.pane.view, Qt.Key.Key_Space)
        self.assertTrue(self.pane.bar.overlay_checked())
        QTest.keyClick(self.pane.view, Qt.Key.Key_Space)
        self.assertFalse(self.pane.bar.overlay_checked())


if __name__ == "__main__":
    unittest.main()
