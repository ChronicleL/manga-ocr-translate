"""Left column: the project's pages as a thumbnail list."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListView,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import Action, CaptionLabel, FluentIcon as FIF, ListWidget, RoundMenu
from qfluentwidgets import StrongBodyLabel

from .ui_common import STATUS_BUSY, STATUS_COLORS, STATUS_LABELS

THUMB_W, THUMB_H = 72, 96
ID_ROLE = Qt.ItemDataRole.UserRole


def _thumbnail_icon(pixmap: QPixmap) -> QIcon:
    """Use the same pixmap in every state so a selected page is not tinted blue."""
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
        icon.addPixmap(pixmap, mode)
    return icon


def _compose_thumbnail(image: Optional[QImage], status: str, dpr: float) -> QPixmap:
    """Page thumbnail centred on a fixed canvas, with a status dot in the corner."""
    canvas = QPixmap(int(THUMB_W * dpr), int(THUMB_H * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if image is not None and not image.isNull():
        scaled = image.scaled(int(THUMB_W * dpr), int(THUMB_H * dpr),
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        # The image is in physical pixels; without this the painter would treat
        # it as logical units and draw it dpr times too large (cropped).
        scaled.setDevicePixelRatio(dpr)
        w, h = scaled.width() / dpr, scaled.height() / dpr
        x, y = (THUMB_W - w) / 2, (THUMB_H - h) / 2
        painter.drawImage(QPoint(round(x), round(y)), scaled)
        painter.setPen(QColor(0, 0, 0, 40))
        painter.drawRect(round(x), round(y), round(w) - 1, round(h) - 1)
    else:
        painter.fillRect(0, 0, THUMB_W, THUMB_H, QColor("#d0d0d0"))
    # Status dot, top-right, with a white ring so it reads on any page.
    cx, cy, r = THUMB_W - 9, 9, 6
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPoint(cx, cy), r + 2, r + 2)
    painter.setBrush(QColor(STATUS_COLORS.get(status, "#8a8a8a")))
    painter.drawEllipse(QPoint(cx, cy), r, r)
    painter.end()
    return canvas


class _PageList(ListWidget):
    """List that reorders internally and reports dropped files."""

    urlsDropped = Signal(list)
    orderChanged = Signal()
    deletePressed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setIconSize(QSize(THUMB_W, THUMB_H))
        self.setSpacing(2)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    @staticmethod
    def _local_files(event) -> List[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event) -> None:
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        files = self._local_files(event)
        if files:
            event.acceptProposedAction()
            self.urlsDropped.emit(files)
            return
        super().dropEvent(event)
        self.orderChanged.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.deletePressed.emit()
            return
        super().keyPressEvent(event)


class PagesPanel(QWidget):
    """Thumbnails with per-page status; selection drives the centre view."""

    pageSelected = Signal(str)      # page id, "" when nothing is selected
    pagesDropped = Signal(list)     # local paths dropped onto the list
    removeRequested = Signal(str)
    reprocessRequested = Signal(str)
    orderChanged = Signal(list)     # new order of page ids

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: Dict[str, QListWidgetItem] = {}
        self._paths: Dict[str, Path] = {}
        self._status: Dict[str, str] = {}
        self._thumb_images: Dict[str, QImage] = {}
        self._queue: Deque[str] = deque()
        # A timer owned by the panel (unlike QTimer.singleShot with a callback) dies with it,
        # so a thumbnail step can never run against an already destroyed panel.
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._load_next_thumbnail)

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 4)
        self._title = StrongBodyLabel("页面", self)
        self._count = CaptionLabel("", self)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._count)

        self.list = _PageList(self)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.urlsDropped.connect(self.pagesDropped)
        self.list.orderChanged.connect(self._on_order_changed)
        self.list.deletePressed.connect(self._request_remove_current)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.list, 1)
        self.setMinimumWidth(150)

    # -- content ------------------------------------------------------------
    def clear(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self._items.clear()
        self._paths.clear()
        self._status.clear()
        self._thumb_images.clear()
        self._queue.clear()
        self._update_count()
        self.pageSelected.emit("")

    def add_page(self, page_id: str, path: Path, status: str) -> None:
        item = QListWidgetItem()
        item.setData(ID_ROLE, page_id)
        item.setSizeHint(QSize(THUMB_W + 110, THUMB_H + 12))
        self._items[page_id] = item
        self._paths[page_id] = Path(path)
        self._status[page_id] = status
        self.list.addItem(item)
        item.setIcon(_thumbnail_icon(_compose_thumbnail(None, status, self._dpr())))
        self._refresh_texts()
        self._queue.append(page_id)
        self._thumb_timer.start()

    def remove_page(self, page_id: str) -> None:
        item = self._items.pop(page_id, None)
        if item is None:
            return
        self.list.takeItem(self.list.row(item))
        for table in (self._paths, self._status, self._thumb_images):
            table.pop(page_id, None)
        self._refresh_texts()

    def update_status(self, page_id: str, status: str) -> None:
        if page_id not in self._items:
            return
        self._status[page_id] = status
        self._redraw_icon(page_id)
        self._refresh_texts()

    def select(self, page_id: str) -> None:
        item = self._items.get(page_id)
        if item is not None:
            self.list.setCurrentItem(item)

    def current_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(ID_ROLE)) if item is not None else ""

    def ids(self) -> List[str]:
        return [str(self.list.item(row).data(ID_ROLE)) for row in range(self.list.count())]

    # -- internals ----------------------------------------------------------
    def _dpr(self) -> float:
        return max(1.0, float(self.devicePixelRatioF()))

    def _redraw_icon(self, page_id: str) -> None:
        item = self._items.get(page_id)
        if item is not None:
            item.setIcon(_thumbnail_icon(_compose_thumbnail(
                self._thumb_images.get(page_id), self._status.get(page_id, ""), self._dpr())))

    def _refresh_texts(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            page_id = str(item.data(ID_ROLE))
            status = self._status.get(page_id, "")
            item.setText(f"第 {row + 1} 页\n{STATUS_LABELS.get(status, '')}")
            item.setToolTip(str(self._paths.get(page_id, "")))
        self._update_count()

    def _update_count(self) -> None:
        self._count.setText(f"{self.list.count()} 页" if self.list.count() else "")

    def _load_next_thumbnail(self) -> None:
        """Read one thumbnail per tick so a big import never freezes the UI."""
        while self._queue:
            page_id = self._queue.popleft()
            path = self._paths.get(page_id)
            if path is None or page_id not in self._items:
                continue
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                dpr = self._dpr()
                size.scale(int(THUMB_W * dpr), int(THUMB_H * dpr),
                           Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(size)
            image = reader.read()
            if not image.isNull():
                self._thumb_images[page_id] = image
                self._redraw_icon(page_id)
            break
        if self._queue:
            self._thumb_timer.start()

    def _on_current_changed(self, current: Optional[QListWidgetItem],
                            _previous: Optional[QListWidgetItem]) -> None:
        self.pageSelected.emit(str(current.data(ID_ROLE)) if current is not None else "")

    def _on_order_changed(self) -> None:
        self._refresh_texts()
        self.orderChanged.emit(self.ids())

    def _request_remove_current(self) -> None:
        page_id = self.current_id()
        if page_id:
            self.removeRequested.emit(page_id)

    def _show_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        page_id = str(item.data(ID_ROLE))
        menu = RoundMenu(parent=self)
        reprocess = Action(FIF.SYNC, "重新识别")
        reprocess.triggered.connect(lambda: self.reprocessRequested.emit(page_id))
        menu.addAction(reprocess)
        remove = Action(FIF.DELETE, "删除此页")
        remove.triggered.connect(lambda: self.removeRequested.emit(page_id))
        menu.addAction(remove)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def busy(self, page_id: str, busy: bool, fallback: str) -> None:
        """Show/clear the transient 'recognising' state of a page."""
        self.update_status(page_id, STATUS_BUSY if busy else fallback)
