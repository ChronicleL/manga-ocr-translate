"""Centre column: zoomable page canvas with text-block outlines and a floating bar."""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import BodyLabel

from .floating_bar import FloatingBar

MIN_SCALE = 0.05
MAX_SCALE = 8.0
ZOOM_STEP = 1.15


class _Badge(QGraphicsItem):
    """The block number above a block's corner; keeps its size at any zoom."""

    HEIGHT = 18

    def __init__(self, text: str, parent: QGraphicsItem) -> None:
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._text = text
        self._color = QColor("#0078d4")
        self._font = QFont("Segoe UI", 8)
        self._font.setBold(True)
        self._width = 12 + 7 * len(text)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, -self.HEIGHT, self._width, self.HEIGHT)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(self.boundingRect(), 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(self._font)
        painter.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, self._text)


class BlockItem(QGraphicsRectItem):
    """Outline of one text block, in image-pixel coordinates."""

    def __init__(self, index: int, rect: QRectF) -> None:
        super().__init__(rect)
        self.index = index
        self.setZValue(1 + index * 1e-3)
        self.badge = _Badge(str(index + 1), self)
        self.badge.setPos(rect.topLeft())
        self._selected = False
        self.set_color(QColor("#0078d4"))

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.badge.set_color(color)
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._restyle()
        self.setZValue(50 if selected else 1 + self.index * 1e-3)

    def _restyle(self) -> None:
        pen = QPen(self._color, 3 if self._selected else 2)
        pen.setCosmetic(True)               # constant on-screen width at any zoom
        self.setPen(pen)
        fill = QColor(self._color)
        fill.setAlpha(70 if self._selected else 22)
        self.setBrush(QBrush(fill))


class CanvasView(QGraphicsView):
    """Zoom with Ctrl+wheel, pan by dragging, double-click to fit, click to select a block."""

    blockClicked = Signal(int)     # block index, -1 when the click hit no block
    zoomChanged = Signal(float)    # 1.0 == one image pixel per logical pixel

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)

        self._original = QPixmap()
        self._overlay = QPixmap()
        self._showing_overlay = False
        self._blocks: list[BlockItem] = []
        self._selected = -1
        self._boxes_visible = True
        self._accent = QColor("#0078d4")
        self._fit_mode = True
        self._press_pos: Optional[QPointF] = None

        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(Qt.BrushStyle.NoBrush)   # the pane behind shows through
        self.viewport().setAutoFillBackground(False)
        self.setObjectName("canvasView")
        self.setStyleSheet("#canvasView { background: transparent; }")
        # Panning is by dragging / wheel; the native scroll bars would clash with the look.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # -- content ------------------------------------------------------------
    def set_page(self, pixmap: QPixmap, blocks: Sequence) -> None:
        """Show a new page (resets zoom to fit and drops any overlay)."""
        self._original = pixmap
        self._overlay = QPixmap()
        self._showing_overlay = False
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.set_blocks(blocks)
        self.select_block(-1)
        self.fit()

    def set_blocks(self, blocks: Sequence) -> None:
        for item in self._blocks:
            self._scene.removeItem(item)
        self._blocks = []
        for index, block in enumerate(blocks):
            x1, y1, x2, y2 = block.bbox
            item = BlockItem(index, QRectF(x1, y1, max(x2 - x1, 1), max(y2 - y1, 1)))
            item.set_color(self._accent)
            item.setVisible(self._boxes_visible)
            self._scene.addItem(item)
            self._blocks.append(item)
        if 0 <= self._selected < len(self._blocks):
            self._blocks[self._selected].set_selected(True)
        else:
            self._selected = -1

    def set_overlay(self, pixmap: QPixmap) -> None:
        self._overlay = pixmap
        self._refresh_pixmap()

    def clear_overlay(self) -> None:
        self.set_overlay(QPixmap())

    def has_overlay(self) -> bool:
        return not self._overlay.isNull()

    def show_overlay(self, show: bool) -> None:
        self._showing_overlay = show
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        use_overlay = self._showing_overlay and self.has_overlay()
        self._item.setPixmap(self._overlay if use_overlay else self._original)

    def is_showing_overlay(self) -> bool:
        return self._showing_overlay and self.has_overlay()

    def set_boxes_visible(self, visible: bool) -> None:
        self._boxes_visible = visible
        for item in self._blocks:
            item.setVisible(visible)

    def set_accent(self, color: QColor) -> None:
        self._accent = color
        for item in self._blocks:
            item.set_color(color)

    def select_block(self, index: int, center: bool = False) -> None:
        if 0 <= self._selected < len(self._blocks):
            self._blocks[self._selected].set_selected(False)
        self._selected = index if 0 <= index < len(self._blocks) else -1
        if self._selected >= 0:
            item = self._blocks[self._selected]
            item.set_selected(True)
            if center:
                self.centerOn(item)

    def reveal_block(self, index: int) -> None:
        """Bring a block into view, moving the canvas only if it is not fully visible.

        Centring on every selection would make the page jump around while the user
        steps through cards or types in one.
        """
        if not 0 <= index < len(self._blocks):
            return
        item = self._blocks[index]
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if not visible.contains(item.sceneBoundingRect()):
            self.centerOn(item)

    @property
    def selected_block(self) -> int:
        return self._selected

    def block_count(self) -> int:
        return len(self._blocks)

    # -- zoom ---------------------------------------------------------------
    def scale_factor(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return
        self._fit_mode = True
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        # fitInView ignores our limits: very small pages would be blown up.
        current = self.scale_factor()
        if current > 1.0:
            self.resetTransform()
        self.zoomChanged.emit(self.scale_factor())

    def zoom_by(self, factor: float, about_center: bool = False) -> None:
        target = min(max(self.scale_factor() * factor, MIN_SCALE), MAX_SCALE)
        factor = target / self.scale_factor()
        if abs(factor - 1.0) < 1e-6:
            return
        self._fit_mode = False
        if about_center:
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(factor, factor)
            self.setTransformationAnchor(anchor)
        else:
            self.scale(factor, factor)
        self.zoomChanged.emit(self.scale_factor())

    # -- events -------------------------------------------------------------
    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_by(ZOOM_STEP ** (delta / 120.0))
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()

    def mouseDoubleClickEvent(self, event) -> None:
        self.fit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        moved = (event.position() - self._press_pos).manhattanLength()
        self._press_pos = None
        if moved > QApplication.startDragDistance():
            return                          # it was a pan, not a click
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, BlockItem):
            item = item.parentItem()
        self.blockClicked.emit(item.index if isinstance(item, BlockItem) else -1)


class CanvasPane(QWidget):
    """The canvas plus the floating bar that hovers at its bottom centre."""

    overlayToggled = Signal(bool)
    boxesToggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.view = CanvasView(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # Shown instead of a page when there is nothing to display.
        self.hint = BodyLabel("", self)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.bar = FloatingBar(self)
        self.bar.overlayToggled.connect(self.overlayToggled)
        self.bar.boxesToggled.connect(self._on_boxes_toggled)
        self.bar.fitRequested.connect(self.view.fit)
        self.bar.zoomInRequested.connect(lambda: self.view.zoom_by(ZOOM_STEP, about_center=True))
        self.bar.zoomOutRequested.connect(lambda: self.view.zoom_by(1 / ZOOM_STEP, about_center=True))
        self.view.zoomChanged.connect(self.bar.set_zoom)

        # Space flips between the original and the overlay while the canvas has focus.
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.bar.toggle_overlay)

    def show_page(self, pixmap: QPixmap, blocks: Sequence) -> None:
        self.hint.hide()
        self.bar.show()
        self.view.set_page(pixmap, blocks)

    def show_message(self, text: str) -> None:
        """Empty state: no page to show (nothing imported, file missing, ...)."""
        self.view.set_page(QPixmap(), [])
        self.hint.setText(text)
        self.hint.show()
        self.bar.hide()

    def _on_boxes_toggled(self, visible: bool) -> None:
        self.view.set_boxes_visible(visible)
        self.boxesToggled.emit(visible)

    def apply_theme(self, dark: bool) -> None:
        self.bar.apply_theme(dark)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.hint.setGeometry(self.rect().adjusted(24, 24, -24, -24))
        self._place_bar()
        self._round_corners()

    def _place_bar(self) -> None:
        self.bar.adjustSize()
        self.bar.move((self.width() - self.bar.width()) // 2,
                      self.height() - self.bar.height() - 18)
        self.bar.raise_()

    def _round_corners(self, radius: int = 7) -> None:
        """Clip the viewport to the rounded card it sits in."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        self.view.viewport().setMask(QRegion(path.toFillPolygon().toPolygon()))
