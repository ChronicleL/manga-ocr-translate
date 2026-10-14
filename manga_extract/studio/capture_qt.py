"""Screen capture: freeze each screen, let the user drag a rectangle, return the crop.

One overlay per screen (not one stitched virtual desktop): every screen is grabbed at its own
scale factor, so mixed-DPI multi-monitor setups map the selection to the right pixels. The
selection stays on the screen it was started on.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap, QScreen
from PySide6.QtWidgets import QWidget

MIN_SELECTION = 8          # logical pixels; anything smaller counts as a mis-click
DIM = QColor(0, 0, 0, 110)
HINT = "拖动鼠标框选要提取的区域　·　Esc / 右键取消"


class RegionOverlay(QWidget):
    """Frozen screenshot of one screen with a drag-to-select rectangle on top."""

    selected = Signal(QImage)      # the cropped pixels (physical resolution)
    cancelled = Signal()

    def __init__(self, geometry: QRect, screenshot: QPixmap, accent: QColor,
                 screen: Optional[QScreen] = None) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        # Must not count as "the last window": the main window is hidden while we are up, and
        # closing the overlays would otherwise quit the whole application.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        if screen is not None:
            self.setScreen(screen)
        self.setGeometry(geometry)

        self._shot = screenshot
        self._dpr = screenshot.devicePixelRatio() or 1.0
        self._accent = accent
        self._start: Optional[QPoint] = None
        self._end: Optional[QPoint] = None
        self._done = False

    # -- selection ----------------------------------------------------------
    def selection(self) -> Optional[QRect]:
        """The dragged rectangle in widget (logical) coordinates, normalised and clamped."""
        if self._start is None or self._end is None:
            return None
        # Built from the corner and the distance dragged. QRect(topLeft, bottomRight) would
        # include both end points and come out one pixel wider and taller than the drag.
        left, top = min(self._start.x(), self._end.x()), min(self._start.y(), self._end.y())
        width, height = abs(self._end.x() - self._start.x()), abs(self._end.y() - self._start.y())
        return QRect(left, top, width, height).intersected(self.rect())

    def crop(self, rect: QRect) -> QImage:
        """The pixels under ``rect`` at the screen's native resolution."""
        dpr = self._dpr
        source = QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                       round(rect.width() * dpr), round(rect.height() * dpr))
        image = self._shot.toImage()
        return image.copy(source.intersected(image.rect()))

    # -- events -------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._finish(None)
        elif event.button() == Qt.MouseButton.LeftButton:
            self._start = self._end = event.position().toPoint()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._end = event.position().toPoint()
        rect = self.selection()
        if rect is None or rect.width() < MIN_SELECTION or rect.height() < MIN_SELECTION:
            self._finish(None)
        else:
            self._finish(self.crop(rect))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
        else:
            super().keyPressEvent(event)

    def _finish(self, image: Optional[QImage]) -> None:
        if self._done:
            return
        self._done = True
        if image is None:
            self.cancelled.emit()
        else:
            self.selected.emit(image)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self._shot)
        painter.fillRect(self.rect(), DIM)
        rect = self.selection()
        if rect is None or rect.isEmpty():
            self._paint_pill(painter, HINT, QPoint(self.width() // 2, 40), centered=True)
            return
        # Undim the selection by drawing the matching part of the screenshot again.
        dpr = self._dpr
        source = QRectF(rect.x() * dpr, rect.y() * dpr, rect.width() * dpr, rect.height() * dpr)
        painter.drawPixmap(QRectF(rect), self._shot, source)
        painter.setPen(QPen(self._accent, 2))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        label = f"{round(rect.width() * dpr)} × {round(rect.height() * dpr)}"
        above = rect.top() > 30
        anchor = QPoint(rect.left(), rect.top() - 6) if above else QPoint(rect.left() + 4, rect.bottom() + 24)
        self._paint_pill(painter, label, anchor, centered=False)

    @staticmethod
    def _paint_pill(painter: QPainter, text: str, anchor: QPoint, centered: bool) -> None:
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 20, metrics.height() + 10
        left = anchor.x() - width // 2 if centered else anchor.x()
        box = QRect(left, anchor.y() - height if not centered else anchor.y() - height // 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 210))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def _grab_screen(screen: QScreen) -> QPixmap:
    return screen.grabWindow(0)


class ScreenCapture(QObject):
    """Hide the main window, freeze the screens, and report the selected region.

    ``grabber`` and ``screens`` can be replaced (tests use fakes; real screens are grabbed with
    ``QScreen.grabWindow``).
    """

    captured = Signal(QImage)
    cancelled = Signal()

    def __init__(self, window: QWidget, accent: QColor,
                 grabber: Callable[[object], QPixmap] = _grab_screen,
                 screens: Optional[Callable[[], Sequence[object]]] = None,
                 delay_ms: int = 180) -> None:
        super().__init__(window)
        self._window = window
        self._accent = accent
        self._grabber = grabber
        self._screens = screens or QGuiApplication.screens
        self._delay = delay_ms
        self._overlays: List[RegionOverlay] = []
        self._starting = False
        self._was_visible = False

    @property
    def active(self) -> bool:
        return self._starting or bool(self._overlays)

    def start(self) -> None:
        if self.active:
            return
        self._starting = True
        self._was_visible = self._window.isVisible()
        self._window.hide()
        # The window needs a moment to really disappear, or it would be in the screenshot.
        QTimer.singleShot(self._delay, self._grab)

    def _grab(self) -> None:
        self._starting = False
        for screen in self._screens():
            pixmap = self._grabber(screen)
            if pixmap is None or pixmap.isNull():
                continue
            overlay = RegionOverlay(screen.geometry(), pixmap, self._accent,
                                    screen if isinstance(screen, QScreen) else None)
            overlay.selected.connect(self._on_selected)
            overlay.cancelled.connect(self._on_cancelled)
            self._overlays.append(overlay)
            overlay.show()
            overlay.raise_()
            overlay.activateWindow()
            overlay.setFocus()
        if not self._overlays:                       # nothing could be grabbed
            self._restore()
            self.cancelled.emit()

    def _on_selected(self, image: QImage) -> None:
        self._end()
        self.captured.emit(image)

    def _on_cancelled(self) -> None:
        self._end()
        self.cancelled.emit()

    def _end(self) -> None:
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            overlay.close()
            overlay.deleteLater()
        self._restore()

    def _restore(self) -> None:
        if self._was_visible:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
