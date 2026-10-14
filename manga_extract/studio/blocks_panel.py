"""Right column: one card per text block, in reading order, with editable text."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon as FIF,
    SmoothScrollArea,
    StrongBodyLabel,
    TextEdit,
    ToolTipFilter,
    TransparentToolButton,
)

MAX_EDIT_LINES = 8


class AutoHeightEdit(TextEdit):
    """Plain-text box that grows with its content (up to a limit, then scrolls)."""

    userChanged = Signal(str)     # only for edits made by the user, not by ``set_text``
    focusedIn = Signal()

    def __init__(self, placeholder: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._programmatic = False
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)           # Tab moves on instead of typing a tab
        self.setPlaceholderText(placeholder)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(self._on_text_changed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def set_text(self, text: str) -> None:
        """Set the text without reporting it as a user edit.

        Surrounding whitespace is ignored when comparing: the model stores stripped text, so a
        space the user is in the middle of typing must not trigger a reset (and lose the caret).
        """
        if self.toPlainText().strip() == text.strip():
            return
        self._programmatic = True
        try:
            self.setPlainText(text)
        finally:
            self._programmatic = False
        self._fit_height()

    def _on_text_changed(self) -> None:
        self._fit_height()
        if not self._programmatic:
            self.userChanged.emit(self.toPlainText())

    def _fit_height(self, *_args) -> None:
        frame = 12                                    # widget frame + stylesheet padding
        margins = int(self.document().documentMargin() * 2)
        line = self.fontMetrics().lineSpacing()
        content = int(self.document().size().height()) + frame   # the document height holds its margins
        low = line + margins + frame
        high = line * MAX_EDIT_LINES + margins + frame
        # Beyond the cap the box scrolls; qfluentwidgets' own overlay scroll bar shows it.
        self.setFixedHeight(max(low, min(content, high)))

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focusedIn.emit()


class BlockCard(QFrame):
    """One text block: original, translation, reviewed tick, re-translate."""

    sourceEdited = Signal(int, str)
    translationEdited = Signal(int, str)
    reviewedToggled = Signal(int, bool)
    retranslateRequested = Signal(int)
    selected = Signal(int)

    def __init__(self, index: int, block, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.index = index
        self._selected = False
        self.setObjectName("blockCard")

        self.badge = StrongBodyLabel(f"#{index + 1}", self)
        self.direction = CaptionLabel("竖排" if block.vertical else "横排", self)
        self.review_box = CheckBox("已校对", self)
        self.review_box.toggled.connect(lambda checked: self.reviewedToggled.emit(self.index, checked))
        self.retranslate_button = TransparentToolButton(FIF.SYNC, self)
        self.retranslate_button.clicked.connect(lambda: self.retranslateRequested.emit(self.index))
        self.retranslate_button.installEventFilter(ToolTipFilter(self.retranslate_button))

        self.source_edit = AutoHeightEdit("原文", self)
        self.target_edit = AutoHeightEdit("译文", self)
        self.source_edit.userChanged.connect(lambda text: self.sourceEdited.emit(self.index, text))
        self.target_edit.userChanged.connect(lambda text: self.translationEdited.emit(self.index, text))
        self.source_edit.focusedIn.connect(lambda: self.selected.emit(self.index))
        self.target_edit.focusedIn.connect(lambda: self.selected.emit(self.index))

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.badge)
        top.addWidget(self.direction)
        top.addStretch(1)
        top.addWidget(self.review_box)
        top.addWidget(self.retranslate_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(CaptionLabel("原文", self))
        layout.addWidget(self.source_edit)
        layout.addSpacing(4)
        layout.addWidget(CaptionLabel("译文", self))
        layout.addWidget(self.target_edit)

        self.sync_from(block)

    def sync_from(self, block) -> None:
        """Show the block's current state without reporting it back as an edit."""
        self.source_edit.set_text(block.source)
        self.target_edit.set_text(block.translation)
        self.update_controls(block)

    def update_controls(self, block) -> None:
        """Refresh the tick and the re-translate button only (never touches the text)."""
        self.review_box.blockSignals(True)
        self.review_box.setChecked(block.reviewed)
        self.review_box.blockSignals(False)
        can_retranslate = block.has_text and not block.reviewed
        self.retranslate_button.setEnabled(can_retranslate)
        self.retranslate_button.setToolTip("重新翻译此块" if can_retranslate
                                           else "已校对的块不会被重新翻译，取消勾选后可用")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected

    def restyle(self, dark: bool, accent: QColor) -> None:
        card = "#363636" if dark else "#f7f7f7"
        line = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.08)"
        border = accent.name() if self._selected else line
        self.setStyleSheet(
            f"#blockCard {{ background: {card}; border: 2px solid {border}; border-radius: 8px; }}")

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self.index)
        super().mousePressEvent(event)


class BlocksPanel(QWidget):
    """Header with review progress, then the block cards in reading order."""

    sourceEdited = Signal(int, str)
    translationEdited = Signal(int, str)
    reviewedToggled = Signal(int, bool)
    retranslateRequested = Signal(int)
    cardSelected = Signal(int)          # user clicked / focused a card

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cards: List[BlockCard] = []
        self._blocks: Sequence = []
        self._selected = -1
        self._dark = False
        self._accent = QColor("#0078d4")

        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 4)
        header.addWidget(StrongBodyLabel("校对", self))
        header.addStretch(1)
        self.progress = CaptionLabel("", self)
        header.addWidget(self.progress)

        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.hint.setContentsMargins(16, 6, 16, 6)

        self._holder = QWidget()
        self._cards_layout = QVBoxLayout(self._holder)
        self._cards_layout.setContentsMargins(12, 6, 12, 12)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch(1)

        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self._holder)
        self.scroll.setStyleSheet("SmoothScrollArea { background: transparent; border: none; }")
        self._holder.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.hint)
        layout.addWidget(self.scroll, 1)
        self.setMinimumWidth(260)
        self.show_message("")

    # -- content ------------------------------------------------------------
    def _clear(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._selected = -1

    def show_message(self, text: str) -> None:
        """Empty state (nothing recognised yet, no text found, ...)."""
        self._clear()
        self._blocks = []
        self.hint.setText(text)
        self.hint.setVisible(bool(text))
        self.progress.setText("")

    def set_blocks(self, blocks: Sequence) -> None:
        """Rebuild the cards for a page. ``blocks`` are the live BlockData objects."""
        self._clear()
        self._blocks = blocks
        if not blocks:
            self.show_message("这一页没有检测到文字。")
            return
        self.hint.hide()
        for index, block in enumerate(blocks):
            card = BlockCard(index, block, self._holder)
            card.sourceEdited.connect(self.sourceEdited)
            card.translationEdited.connect(self.translationEdited)
            card.reviewedToggled.connect(self._on_reviewed)
            card.retranslateRequested.connect(self.retranslateRequested)
            card.selected.connect(self._on_card_selected)
            card.restyle(self._dark, self._accent)
            self._cards_layout.insertWidget(len(self._cards), card)
            self._cards.append(card)
        self._update_progress()

    def refresh(self, blocks: Sequence) -> None:
        """Bring the cards in line with changed data (keeps focus and the caret)."""
        if len(blocks) != len(self._cards):
            self.set_blocks(blocks)
            return
        self._blocks = blocks
        for card, block in zip(self._cards, blocks):
            card.sync_from(block)
        self._update_progress()

    def select(self, index: int, scroll: bool = True) -> None:
        """Highlight a card (programmatic: does not emit ``cardSelected``)."""
        self._selected = index if 0 <= index < len(self._cards) else -1
        for card in self._cards:
            card.set_selected(card.index == self._selected)
            card.restyle(self._dark, self._accent)
        if scroll and self._selected >= 0:
            self.scroll.ensureWidgetVisible(self._cards[self._selected], 0, 16)

    @property
    def selected_index(self) -> int:
        return self._selected

    def card_count(self) -> int:
        return len(self._cards)

    def card(self, index: int) -> BlockCard:
        return self._cards[index]

    # -- internals ----------------------------------------------------------
    def _on_card_selected(self, index: int) -> None:
        if index == self._selected:
            return
        self.select(index, scroll=False)
        self.cardSelected.emit(index)

    def _on_reviewed(self, index: int, checked: bool) -> None:
        self.reviewedToggled.emit(index, checked)
        self._update_progress()

    def _update_progress(self) -> None:
        # Count the ticks on the cards themselves: they are always current, whereas the model
        # object only changes once a listener of ``reviewedToggled`` has handled the signal.
        total = sum(1 for block in self._blocks if block.has_text)
        done = sum(1 for card, block in zip(self._cards, self._blocks)
                   if block.has_text and card.review_box.isChecked())
        self.progress.setText(f"已校对 {done}/{total}" if total else "")

    def apply_theme(self, dark: bool, accent: QColor) -> None:
        self._dark, self._accent = dark, accent
        for card in self._cards:
            card.restyle(dark, accent)
