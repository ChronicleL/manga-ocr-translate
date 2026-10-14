"""The small pill-shaped toolbar that hovers over the canvas."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    CaptionLabel,
    CheckableMenu,
    FluentIcon as FIF,
    MenuIndicatorType,
    ToolTipFilter,
    TransparentDropDownPushButton,
    TransparentToggleToolButton,
    TransparentToolButton,
    VerticalSeparator,
)

from .shortcuts import hint

# (key, label, enabled, tooltip) for one translation channel entry.
Channel = Tuple[str, str, bool, str]


class FloatingBar(QFrame):
    """Translate menu, original/overlay switch, outline toggle and zoom controls."""

    translatePageRequested = Signal()
    translateAllRequested = Signal()
    translatePendingRequested = Signal()
    translateCancelRequested = Signal()
    channelChosen = Signal(str)
    overlayToggled = Signal(bool)
    boxesToggled = Signal(bool)
    fitRequested = Signal()
    zoomInRequested = Signal()
    zoomOutRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("floatBar")
        self._busy = False
        self._channels: List[Channel] = []
        self._current_channel = ""

        self.translate_button = TransparentDropDownPushButton(FIF.LANGUAGE, "翻译", self)
        self._rebuild_menu()

        self.overlay_button = TransparentToggleToolButton(FIF.PHOTO, self)
        self.overlay_button.toggled.connect(self.overlayToggled)
        self.boxes_button = TransparentToggleToolButton(FIF.VIEW, self)
        self.boxes_button.setChecked(True)
        self.boxes_button.toggled.connect(self.boxesToggled)

        self.zoom_out_button = TransparentToolButton(FIF.ZOOM_OUT, self)
        self.zoom_out_button.clicked.connect(self.zoomOutRequested)
        self.zoom_label = CaptionLabel("100%", self)
        self.zoom_label.setFixedWidth(46)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = TransparentToolButton(FIF.ZOOM_IN, self)
        self.zoom_in_button.clicked.connect(self.zoomInRequested)
        self.fit_button = TransparentToolButton(FIF.FIT_PAGE, self)
        self.fit_button.clicked.connect(self.fitRequested)

        for button, tip in (
            (self.boxes_button, "显示 / 隐藏文本块框"),
            (self.zoom_out_button, "缩小（Ctrl + 滚轮）"),
            (self.zoom_in_button, "放大（Ctrl + 滚轮）"),
            (self.fit_button, "适应窗口（双击画布）"),
        ):
            button.setToolTip(tip)
            button.installEventFilter(ToolTipFilter(button))
        self.overlay_button.installEventFilter(ToolTipFilter(self.overlay_button))
        self.set_overlay_state(False, "翻译后可在原图与叠图之间切换")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(2)
        row.addWidget(self.translate_button)
        row.addWidget(VerticalSeparator(self))
        row.addWidget(self.overlay_button)
        row.addWidget(self.boxes_button)
        row.addWidget(VerticalSeparator(self))
        row.addWidget(self.zoom_out_button)
        row.addWidget(self.zoom_label)
        row.addWidget(self.zoom_in_button)
        row.addWidget(self.fit_button)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)
        self.apply_theme(False)

    # -- translate menu -----------------------------------------------------
    def set_channels(self, channels: List[Channel], current: str) -> None:
        """Entries of the channel radio group; ``current`` is the chosen key."""
        self._channels, self._current_channel = list(channels), current
        self._rebuild_menu()

    def set_translating(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.translate_button.setText(text or ("翻译中…" if busy else "翻译"))
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        menu = CheckableMenu(parent=self, indicatorType=MenuIndicatorType.RADIO)
        for text, icon, signal in (
            (hint("翻译本页", "translate_current"), FIF.PLAY, self.translatePageRequested),
            (hint("翻译全部页面", "translate_all"), FIF.SEND, self.translateAllRequested),
            ("只翻译还没有译文的页面", FIF.SYNC, self.translatePendingRequested),
        ):
            action = Action(icon, text)
            action.triggered.connect(lambda _checked=False, sig=signal: sig.emit())
            action.setEnabled(not self._busy)
            menu.addAction(action)
        if self._busy:
            cancel = Action(FIF.CANCEL, "取消翻译")
            cancel.triggered.connect(lambda _checked=False: self.translateCancelRequested.emit())
            menu.addAction(cancel)
        if self._channels:
            menu.addSeparator()
            heading = Action("翻译通道")
            heading.setEnabled(False)
            menu.addAction(heading)
            group = QActionGroup(menu)
            group.setExclusive(True)
            for key, label, enabled, tip in self._channels:
                action = Action(label)
                action.setCheckable(True)
                action.setChecked(key == self._current_channel)
                action.setEnabled(enabled and not self._busy)
                if tip:
                    action.setToolTip(tip)
                action.triggered.connect(lambda _checked=False, k=key: self.channelChosen.emit(k))
                group.addAction(action)
                menu.addAction(action)
        self.translate_button.setMenu(menu)

    # -- state --------------------------------------------------------------
    def set_overlay_state(self, available: bool, tip: str, checked: bool = False) -> None:
        """Enable the original/overlay switch; ``checked`` shows the overlay."""
        self.overlay_button.blockSignals(True)
        self.overlay_button.setEnabled(available)
        self.overlay_button.setChecked(available and checked)
        self.overlay_button.blockSignals(False)
        self.overlay_button.setToolTip(tip)

    def toggle_overlay(self) -> None:
        if self.overlay_button.isEnabled():
            self.overlay_button.toggle()

    def overlay_checked(self) -> bool:
        return self.overlay_button.isChecked()

    def set_zoom(self, scale: float) -> None:
        self.zoom_label.setText(f"{round(scale * 100)}%")

    def apply_theme(self, dark: bool) -> None:
        background = "rgba(43, 43, 43, 0.94)" if dark else "rgba(255, 255, 255, 0.94)"
        border = "rgba(255, 255, 255, 0.10)" if dark else "rgba(0, 0, 0, 0.10)"
        self.setStyleSheet(
            f"#floatBar {{ background: {background}; border: 1px solid {border};"
            " border-radius: 21px; }"
        )
