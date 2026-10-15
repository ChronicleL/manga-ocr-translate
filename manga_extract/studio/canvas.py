"""Centre column: zoomable page canvas with text-block outlines and a floating bar."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

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

from qfluentwidgets import Action, BodyLabel, FluentIcon as FIF, RoundMenu

from .floating_bar import FloatingBar

MIN_SCALE = 0.05
MAX_SCALE = 8.0
ZOOM_STEP = 1.15
_HANDLE_SIZE = 7          # screen pixels; constant because handles ignore transforms
_MIN_BOX_PX  = 8          # minimum image-pixel dimension enforced during drag
_DEFAULT_BOX = 80         # size (in image pixels) of a newly-added block

# Eight handle positions and their drag semantics.
# (anchor_x, anchor_y, moves_left, moves_top, moves_right, moves_bottom, cursor)
_HANDLES: Tuple = (
    (0.0, 0.0, True,  True,  False, False, Qt.CursorShape.SizeFDiagCursor),
    (0.5, 0.0, False, True,  False, False, Qt.CursorShape.SizeVerCursor),
    (1.0, 0.0, False, True,  True,  False, Qt.CursorShape.SizeBDiagCursor),
    (0.0, 0.5, True,  False, False, False, Qt.CursorShape.SizeHorCursor),
    (1.0, 0.5, False, False, True,  False, Qt.CursorShape.SizeHorCursor),
    (0.0, 1.0, True,  False, False, True,  Qt.CursorShape.SizeBDiagCursor),
    (0.5, 1.0, False, False, False, True,  Qt.CursorShape.SizeVerCursor),
    (1.0, 1.0, False, False, True,  True,  Qt.CursorShape.SizeFDiagCursor),
)


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


class _Handle(QGraphicsRectItem):
    """One resize handle child of a BlockItem; ignores view transformation."""

    def __init__(self, handle_idx: int, parent: "BlockItem") -> None:
        sz = _HANDLE_SIZE
        super().__init__(-sz / 2, -sz / 2, sz, sz, parent)
        ax, ay, self.cl, self.ct, self.cr, self.cb, cursor = _HANDLES[handle_idx]
        self.anchor_x = ax
        self.anchor_y = ay
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(101)
        self.setPen(QPen(QColor("#0078d4"), 1.5))
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setCursor(cursor)
        self.setVisible(False)

    def reposition(self, rect: QRectF) -> None:
        self.setPos(rect.x() + self.anchor_x * rect.width(),
                    rect.y() + self.anchor_y * rect.height())


class BlockItem(QGraphicsRectItem):
    """Outline of one text block, in image-pixel coordinates."""

    def __init__(self, index: int, rect: QRectF) -> None:
        super().__init__(rect)
        self.index = index
        self.setZValue(1 + index * 1e-3)
        self.badge = _Badge(str(index + 1), self)
        self.badge.setPos(rect.topLeft())
        self._selected = False
        self._handles = [_Handle(i, self) for i in range(8)]
        self.set_color(QColor("#0078d4"))

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.badge.set_color(color)
        for h in self._handles:
            h.setPen(QPen(color, 1.5))
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._restyle()
        self.setZValue(50 if selected else 1 + self.index * 1e-3)
        for h in self._handles:
            h.setVisible(selected)
        if selected:
            self._reposition_handles()

    def _restyle(self) -> None:
        pen = QPen(self._color, 3 if self._selected else 2)
        pen.setCosmetic(True)               # constant on-screen width at any zoom
        self.setPen(pen)
        fill = QColor(self._color)
        fill.setAlpha(70 if self._selected else 22)
        self.setBrush(QBrush(fill))

    def update_rect(self, rect: QRectF) -> None:
        """Change the displayed rect and reposition badge + handles."""
        self.setRect(rect)
        self.badge.setPos(rect.topLeft())
        if self._selected:
            self._reposition_handles()

    def _reposition_handles(self) -> None:
        r = self.rect()
        for h in self._handles:
            h.reposition(r)


class CanvasView(QGraphicsView):
    """Zoom with Ctrl+wheel, pan by dragging, double-click to fit, click to select a block."""

    blockClicked = Signal(int)          # block index, -1 when the click hit no block
    zoomChanged = Signal(float)         # 1.0 == one image pixel per logical pixel
    # Box-editing signals
    blockGeometryChanged = Signal(int, int, int, int, int)  # index, x1, y1, x2, y2
    blockAddRequested    = Signal(int, int, int, int)        # x1, y1, x2, y2 (image coords)
    blockDeleteRequested = Signal(int)                       # block index
    blockRedetectRequested = Signal(int)                     # block index

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
        self._page_rect = QRectF()

        # Handle/block drag state
        self._drag_block  = -1           # index of block being dragged (-1 = none)
        self._drag_handle = -1           # handle index; -1 = move whole block
        self._drag_scene_start = QPointF()
        self._drag_rect_start  = QRectF()

        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(Qt.BrushStyle.NoBrush)
        self.viewport().setAutoFillBackground(False)
        self.setObjectName("canvasView")
        self.setStyleSheet("#canvasView { background: transparent; }")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    # -- content ------------------------------------------------------------
    def set_page(self, pixmap: QPixmap, blocks: Sequence) -> None:
        """Show a new page (resets zoom to fit and drops any overlay)."""
        self._original = pixmap
        self._overlay = QPixmap()
        self._showing_overlay = False
        self._page_rect = QRectF(pixmap.rect()) if not pixmap.isNull() else QRectF()
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
        """Bring a block into view, moving the canvas only if it is not fully visible."""
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

    # -- drag helpers --------------------------------------------------------
    def _item_at_viewport(self, vp_pos):
        """Walk up from itemAt() result to the first BlockItem or _Handle."""
        item = self.itemAt(vp_pos.toPoint())
        while item is not None and not isinstance(item, (BlockItem, _Handle)):
            item = item.parentItem()
        return item

    def _start_drag(self, block_item: BlockItem, handle_idx: int,
                    scene_pos: QPointF) -> None:
        self._drag_block  = block_item.index
        self._drag_handle = handle_idx
        self._drag_scene_start = scene_pos
        self._drag_rect_start  = QRectF(block_item.rect())
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def _apply_drag(self, scene_pos: QPointF) -> None:
        if not 0 <= self._drag_block < len(self._blocks):
            return
        dx = scene_pos.x() - self._drag_scene_start.x()
        dy = scene_pos.y() - self._drag_scene_start.y()
        r  = QRectF(self._drag_rect_start)
        if self._drag_handle == -1:
            # Move entire block
            r.translate(dx, dy)
        else:
            _ax, _ay, cl, ct, cr, cb, _cursor = _HANDLES[self._drag_handle]
            if cl: r.setLeft(r.left()   + dx)
            if ct: r.setTop(r.top()     + dy)
            if cr: r.setRight(r.right() + dx)
            if cb: r.setBottom(r.bottom() + dy)
        # Enforce minimum size
        if r.width()  < _MIN_BOX_PX: r.setWidth(_MIN_BOX_PX)
        if r.height() < _MIN_BOX_PX: r.setHeight(_MIN_BOX_PX)
        # Clamp to page bounds
        if not self._page_rect.isEmpty():
            r = r.intersected(self._page_rect)
            if r.width()  < _MIN_BOX_PX: r.setWidth(_MIN_BOX_PX)
            if r.height() < _MIN_BOX_PX: r.setHeight(_MIN_BOX_PX)
        self._blocks[self._drag_block].update_rect(r)

    def _finish_drag(self) -> None:
        if 0 <= self._drag_block < len(self._blocks):
            r  = self._blocks[self._drag_block].rect()
            r0 = self._drag_rect_start
            x1, y1, x2, y2 = (int(round(v)) for v in (r.left(), r.top(), r.right(), r.bottom()))
            if (x1, y1, x2, y2) != (int(round(r0.left())), int(round(r0.top())),
                                     int(round(r0.right())), int(round(r0.bottom()))):
                self.blockGeometryChanged.emit(self._drag_block, x1, y1, x2, y2)
        self._drag_block  = -1
        self._drag_handle = -1
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

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
            hit = self._item_at_viewport(event.position())
            if isinstance(hit, _Handle):
                block_item = hit.parentItem()
                if isinstance(block_item, BlockItem) and block_item._selected:
                    self._start_drag(block_item, block_item._handles.index(hit),
                                     self.mapToScene(event.position().toPoint()))
                    event.accept()
                    return
            elif isinstance(hit, BlockItem) and hit._selected:
                # Drag the interior → move the entire block
                self._start_drag(hit, -1, self.mapToScene(event.position().toPoint()))
                event.accept()
                return
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_block >= 0:
            self._apply_drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_block >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        moved = (event.position() - self._press_pos).manhattanLength()
        self._press_pos = None
        if moved > QApplication.startDragDistance():
            return
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, BlockItem):
            item = item.parentItem()
        self.blockClicked.emit(item.index if isinstance(item, BlockItem) else -1)

    def contextMenuEvent(self, event) -> None:
        hit = self._item_at_viewport(QPointF(event.pos()))
        while isinstance(hit, _Handle):
            hit = hit.parentItem()
        menu = RoundMenu(parent=self)
        if isinstance(hit, BlockItem):
            redetect = Action(FIF.SYNC, "重新检测此框")
            redetect.triggered.connect(lambda: self.blockRedetectRequested.emit(hit.index))
            menu.addAction(redetect)
            menu.addSeparator()
            delete = Action(FIF.DELETE, "删除此框")
            delete.triggered.connect(lambda: self.blockDeleteRequested.emit(hit.index))
            menu.addAction(delete)
        else:
            sc = self.mapToScene(event.pos())
            x, y = sc.x(), sc.y()
            half = _DEFAULT_BOX / 2
            x1, y1 = int(x - half), int(y - half)
            x2, y2 = int(x + half), int(y + half)
            add = Action(FIF.ADD, "在此新增文字框")
            add.triggered.connect(lambda: self.blockAddRequested.emit(x1, y1, x2, y2))
            menu.addAction(add)
        menu.exec(event.globalPos())


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
