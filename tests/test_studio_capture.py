"""Screen capture overlay and controller, driven with fake screens (offscreen)."""

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
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QImage, QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    from _studio_fakes import dispose_widget, wait_until

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


def coordinate_pixmap(width: int, height: int, dpr: float = 1.0) -> "QPixmap":
    """A screenshot whose pixel (x, y) has colour (x % 256, y % 256, 7): easy to verify crops."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(x, y, QColor(x % 256, y % 256, 7))
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class FakeScreen:
    def __init__(self, geometry: "QRect", dpr: float = 1.0):
        self._geometry, self.dpr = geometry, dpr

    def geometry(self):
        return self._geometry

    def pixmap(self):
        g = self._geometry
        return coordinate_pixmap(round(g.width() * self.dpr), round(g.height() * self.dpr), self.dpr)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RegionOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def overlay(self, dpr: float = 1.0, geometry=None):
        from manga_extract.studio.capture_qt import RegionOverlay

        geometry = geometry or QRect(0, 0, 400, 300)
        overlay = RegionOverlay(geometry, coordinate_pixmap(round(geometry.width() * dpr),
                                                            round(geometry.height() * dpr), dpr),
                                QColor("#0078d4"))
        overlay.show()
        self.addCleanup(dispose_widget, self.app, overlay)
        self.app.processEvents()
        self.selected, self.cancelled = [], []
        overlay.selected.connect(self.selected.append)
        overlay.cancelled.connect(lambda: self.cancelled.append(True))
        return overlay

    def drag(self, overlay, start, end, button=Qt.MouseButton.LeftButton):
        QTest.mousePress(overlay, button, Qt.KeyboardModifier.NoModifier, QPoint(*start))
        QTest.mouseMove(overlay, QPoint(*end))
        QTest.mouseRelease(overlay, button, Qt.KeyboardModifier.NoModifier, QPoint(*end))

    def color(self, image, x, y):
        c = image.pixelColor(x, y)
        return (c.red(), c.green(), c.blue())

    # -- cropping -----------------------------------------------------------
    def test_selection_returns_exactly_the_pixels_under_it(self):
        overlay = self.overlay()
        self.drag(overlay, (50, 40), (150, 120))
        self.assertEqual(len(self.selected), 1)
        image = self.selected[0]
        self.assertEqual((image.width(), image.height()), (100, 80))
        self.assertEqual(self.color(image, 0, 0), (50, 40, 7))              # top-left of the crop
        self.assertEqual(self.color(image, 99, 79), (149, 119, 7))          # bottom-right

    def test_on_a_2x_screen_the_crop_is_in_physical_pixels(self):
        overlay = self.overlay(dpr=2.0)
        self.drag(overlay, (50, 40), (150, 120))                            # logical coordinates
        image = self.selected[0]
        self.assertEqual((image.width(), image.height()), (200, 160))       # 2x the logical size
        self.assertEqual(self.color(image, 0, 0), (100, 80, 7))             # physical (100, 80)
        self.assertEqual(self.color(image, 199, 159), (299 % 256, 239, 7))

    def test_fractional_scaling_rounds_to_whole_pixels(self):
        overlay = self.overlay(dpr=1.5)
        self.drag(overlay, (40, 40), (140, 100))
        image = self.selected[0]
        self.assertEqual((image.width(), image.height()), (150, 90))
        self.assertEqual(self.color(image, 0, 0), (60, 60, 7))

    def test_dragging_backwards_selects_the_same_region(self):
        overlay = self.overlay()
        self.drag(overlay, (150, 120), (50, 40))
        image = self.selected[0]
        self.assertEqual((image.width(), image.height()), (100, 80))
        self.assertEqual(self.color(image, 0, 0), (50, 40, 7))

    def test_dragging_past_the_edge_is_clamped_to_the_screen(self):
        overlay = self.overlay()
        self.drag(overlay, (300, 200), (900, 700))
        image = self.selected[0]
        self.assertEqual((image.width(), image.height()), (100, 100))       # up to 400 x 300
        self.assertEqual(self.color(image, 0, 0), (300 % 256, 200, 7))

    def test_overlay_not_at_the_origin_still_uses_local_coordinates(self):
        overlay = self.overlay(geometry=QRect(1920, 100, 400, 300))
        self.drag(overlay, (10, 20), (60, 70))
        self.assertEqual(self.color(self.selected[0], 0, 0), (10, 20, 7))

    # -- cancelling ---------------------------------------------------------
    def test_a_tiny_selection_or_a_plain_click_cancels(self):
        for end in ((54, 44), (50, 40)):
            overlay = self.overlay()
            self.drag(overlay, (50, 40), end)
            self.assertEqual((self.selected, self.cancelled), ([], [True]), end)

    def test_selection_is_judged_in_logical_pixels(self):
        self.assertEqual(self.overlay_min_selection(), 8)

    def overlay_min_selection(self):
        from manga_extract.studio.capture_qt import MIN_SELECTION

        return MIN_SELECTION

    def test_a_selection_just_over_the_minimum_is_accepted(self):
        overlay = self.overlay()
        self.drag(overlay, (50, 40), (58, 48))                              # 8 x 8
        self.assertEqual(len(self.selected), 1)

    def test_escape_cancels(self):
        overlay = self.overlay()
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.assertEqual((self.selected, self.cancelled), ([], [True]))

    def test_right_click_cancels_even_in_the_middle_of_a_drag(self):
        overlay = self.overlay()
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 40))
        QTest.mouseClick(overlay, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 60))
        self.assertEqual((self.selected, self.cancelled), ([], [True]))

    def test_it_reports_once_no_matter_what_comes_after(self):
        overlay = self.overlay()
        self.drag(overlay, (50, 40), (150, 120))
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.drag(overlay, (10, 10), (90, 90))
        self.assertEqual((len(self.selected), self.cancelled), (1, []))

    # -- misc ---------------------------------------------------------------
    def test_painting_works_before_and_during_a_selection(self):
        overlay = self.overlay()
        before = overlay.grab()
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 40))
        QTest.mouseMove(overlay, QPoint(200, 150))
        during = overlay.grab()
        self.assertEqual((before.width(), before.height()), (400, 300))
        self.assertNotEqual(before.toImage(), during.toImage())            # the rectangle is drawn

    def test_the_selection_is_not_dimmed_but_the_rest_is(self):
        overlay = self.overlay()
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 40))
        QTest.mouseMove(overlay, QPoint(200, 150))
        frame = overlay.grab().toImage()
        inside, outside = frame.pixelColor(120, 90), frame.pixelColor(300, 250)
        self.assertEqual((inside.red(), inside.green()), (120, 90))         # untouched source pixel
        self.assertLess(outside.red(), 300 % 256)                           # dimmed
        self.assertLess(outside.green(), 250)

    def test_it_does_not_keep_the_application_alive_or_quit_it(self):
        overlay = self.overlay()
        self.assertFalse(overlay.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ScreenCaptureControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(300, 200)
        self.window.show()
        self.addCleanup(dispose_widget, self.app, self.window)
        self.app.processEvents()
        self.screens = [FakeScreen(QRect(0, 0, 400, 300)), FakeScreen(QRect(400, 0, 400, 300), 2.0)]
        self.captured, self.cancelled = [], []

    def capture(self, screens=None, grabber=None):
        from manga_extract.studio.capture_qt import ScreenCapture

        controller = ScreenCapture(self.window, QColor("#0078d4"),
                                   grabber=grabber or (lambda s: s.pixmap()),
                                   screens=lambda: self.screens if screens is None else screens,
                                   delay_ms=10)
        controller.captured.connect(self.captured.append)
        controller.cancelled.connect(lambda: self.cancelled.append(True))
        return controller

    def drag(self, overlay, start, end):
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*start))
        QTest.mouseMove(overlay, QPoint(*end))
        QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*end))

    def test_the_window_is_hidden_while_capturing_and_comes_back_after(self):
        controller = self.capture()
        controller.start()
        self.assertFalse(self.window.isVisible())                           # before the grab
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        self.assertFalse(self.window.isVisible())                           # still hidden
        self.drag(controller._overlays[0], (10, 10), (110, 90))
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.captured), 1)
        self.assertFalse(controller.active)

    def test_one_overlay_per_screen_each_with_its_own_scale(self):
        controller = self.capture()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        first, second = controller._overlays
        self.assertEqual((first.geometry().x(), second.geometry().x()), (0, 400))
        self.assertEqual((first._dpr, second._dpr), (1.0, 2.0))

    def test_selecting_on_the_second_screen_uses_that_screens_pixels(self):
        controller = self.capture()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        self.drag(controller._overlays[1], (10, 10), (60, 50))              # 2x screen
        image = self.captured[0]
        self.assertEqual((image.width(), image.height()), (100, 80))
        self.assertEqual(image.pixelColor(0, 0).red(), 20)                  # physical x = 10 * 2

    def test_all_overlays_close_after_a_selection(self):
        controller = self.capture()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        overlays = list(controller._overlays)
        self.drag(overlays[0], (10, 10), (110, 90))
        self.app.processEvents()
        self.assertFalse(any(o.isVisible() for o in overlays))
        self.assertEqual(controller._overlays, [])

    def test_cancelling_restores_the_window_and_reports_it(self):
        controller = self.capture()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        QTest.keyClick(controller._overlays[1], Qt.Key.Key_Escape)
        self.assertEqual((self.captured, self.cancelled), ([], [True]))
        self.assertTrue(self.window.isVisible())

    def test_a_window_that_was_hidden_stays_hidden(self):
        self.window.hide()
        controller = self.capture()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        QTest.keyClick(controller._overlays[0], Qt.Key.Key_Escape)
        self.assertFalse(self.window.isVisible())

    def test_starting_twice_does_not_open_a_second_set_of_overlays(self):
        controller = self.capture()
        controller.start()
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
        controller.start()
        self.app.processEvents()
        self.assertEqual(len(controller._overlays), 2)

    def test_a_screen_that_cannot_be_grabbed_is_skipped(self):
        controller = self.capture(grabber=lambda s: s.pixmap() if s is self.screens[0] else QPixmap())
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: controller._overlays))
        self.assertEqual(len(controller._overlays), 1)
        QTest.keyClick(controller._overlays[0], Qt.Key.Key_Escape)

    def test_nothing_to_grab_cancels_and_restores_the_window(self):
        controller = self.capture(screens=[])
        controller.start()
        self.assertTrue(wait_until(self.app, lambda: self.cancelled))
        self.assertTrue(self.window.isVisible())

    def test_it_can_be_used_again_after_finishing(self):
        controller = self.capture()
        for _ in range(2):
            controller.start()
            self.assertTrue(wait_until(self.app, lambda: len(controller._overlays) == 2))
            self.drag(controller._overlays[0], (10, 10), (110, 90))
        self.assertEqual(len(self.captured), 2)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ImagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_png_round_trip(self):
        from manga_extract.studio.imaging import qimage_to_png

        image = coordinate_pixmap(30, 20).toImage()
        data = qimage_to_png(image)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        back = QImage.fromData(data)
        self.assertEqual((back.width(), back.height()), (30, 20))
        self.assertEqual(back.pixelColor(7, 9).getRgb()[:3], (7, 9, 7))

    def test_a_null_image_is_refused(self):
        from manga_extract.studio.imaging import qimage_to_png

        with self.assertRaises(ValueError):
            qimage_to_png(QImage())


if __name__ == "__main__":
    unittest.main()
