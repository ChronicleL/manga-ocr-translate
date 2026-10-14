"""Dialog: type a chapter URL, review the images found on the page, import the ones you tick."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SmoothScrollArea,
    StrongBodyLabel,
    isDarkTheme,
)

from .ui_common import BG_DARK, BG_LIGHT
from .web_import import DownloadedImage, ImageCandidate, WebImportError, normalize_url
from .web_worker import THUMB_SIZE, WebImporter

COLUMNS = 4
DEFAULT_MIN_WIDTH = 300
NO_IMAGES_HINT = ("没有在这个网页里找到图片。页面可能是用 JavaScript 动态加载的，"
                  "这种情况可以改用「截图」，或先在浏览器里把图片另存下来再导入。")


def _human_size(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):.1f} MB" if nbytes >= 1024 * 1024 else f"{max(1, nbytes // 1024)} KB"


class ThumbCard(QFrame):
    """One candidate image: thumbnail, size line and an import tick."""

    def __init__(self, index: int, candidate: ImageCandidate, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("thumbCard")
        self.index = index
        self.url = candidate.url
        self.state = "loading"            # loading | loaded | failed
        self.width_px = 0
        self.path = ""
        self.suffix = ""

        self.picture = BodyLabel("加载中…", self)       # themed text colour (plain QLabel is not)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setFixedSize(THUMB_SIZE[0], THUMB_SIZE[1])
        self.picture.setWordWrap(True)
        self.caption = CaptionLabel(f"#{index + 1}", self)
        self.caption.setWordWrap(True)
        self.check = CheckBox("导入", self)
        self.check.setEnabled(False)
        self.setToolTip(candidate.url)
        self.setFixedWidth(THUMB_SIZE[0] + 24)        # equal columns; long messages wrap

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self.picture)
        layout.addWidget(self.caption)
        layout.addWidget(self.check)

    def reset(self) -> None:
        self.state, self.width_px, self.path, self.suffix = "loading", 0, "", ""
        self.picture.setPixmap(QPixmap())
        self.picture.setText("加载中…")
        self.caption.setText(f"#{self.index + 1}")
        self.check.setChecked(False)
        self.check.setEnabled(False)

    def set_loaded(self, thumb: QImage, width: int, height: int, nbytes: int, path: str,
                   suffix: str) -> None:
        self.state, self.width_px, self.path, self.suffix = "loaded", width, path, suffix
        self.picture.setPixmap(QPixmap.fromImage(thumb))
        self.caption.setText(f"#{self.index + 1} · {width}×{height} · {_human_size(nbytes)}")
        self.check.setEnabled(True)

    def set_failed(self, message: str) -> None:
        self.state = "failed"
        self.picture.setPixmap(QPixmap())
        self.picture.setText("加载失败")
        self.caption.setText(f"#{self.index + 1} · {message}")
        self.check.setChecked(False)
        self.check.setEnabled(False)

    @property
    def importable(self) -> bool:
        return self.state == "loaded" and self.check.isChecked()


class WebImportDialog(QDialog):
    """After ``exec()`` returns accepted, ``images`` holds the chosen pictures in page order."""

    def __init__(self, parent: Optional[QWidget] = None, initial_url: str = "",
                 importer: Optional[WebImporter] = None,
                 min_width: int = DEFAULT_MIN_WIDTH) -> None:
        super().__init__(parent)
        self.setWindowTitle("从网页导入图片")
        self.setModal(True)
        self.resize(COLUMNS * 196 + 90, 720)
        self.setMinimumSize(560, 460)

        self.importer = importer or WebImporter()
        self.images: List[DownloadedImage] = []
        self.skipped = 0                  # ticked images whose temp file could not be read
        self._cards: List[ThumbCard] = []
        self._candidates: List[ImageCandidate] = []
        self._page_url = ""
        self._folder = Path(tempfile.mkdtemp(prefix="manga_web_"))

        self.url_edit = LineEdit(self)
        self.url_edit.setPlaceholderText("粘贴章节网址，例如 https://example.com/manga/ch1")
        self.url_edit.setText(initial_url)
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self.parse)
        self.parse_button = PrimaryPushButton("解析", self)
        self.parse_button.clicked.connect(self.parse)

        self.referer_edit = LineEdit(self)
        self.referer_edit.setPlaceholderText("Referer，留空则使用网页地址（图片加载失败时可以改这里）")
        self.reload_button = PushButton("重新加载图片", self)
        self.reload_button.setEnabled(False)
        self.reload_button.clicked.connect(self.reload_images)

        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        holder = QWidget()
        holder.setLayout(self.grid)
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(holder)
        self.scroll.setStyleSheet("SmoothScrollArea { background: transparent; border: none; }")
        holder.setStyleSheet("background: transparent;")
        self.empty_hint = CaptionLabel("输入网址并点「解析」，页面里的图片会列在这里。", self)
        self.empty_hint.setWordWrap(True)

        self.summary = CaptionLabel("", self)
        self.select_all_button = PushButton("全选", self)
        self.select_none_button = PushButton("全不选", self)
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        self.select_none_button.clicked.connect(lambda: self._set_all(False))
        self.min_width_edit = LineEdit(self)
        self.min_width_edit.setValidator(QIntValidator(0, 99999, self))
        self.min_width_edit.setFixedWidth(84)
        self.min_width_edit.setText(str(min_width))
        self.min_width_edit.textChanged.connect(lambda _text: self._apply_min_width())

        self.progress = ProgressBar(self)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = CaptionLabel("", self)
        self.cancel_button = PushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)
        self.import_button = PrimaryPushButton("导入所选", self)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.accept)

        row_url = QHBoxLayout()
        row_url.addWidget(self.url_edit, 1)
        row_url.addWidget(self.parse_button)
        row_referer = QHBoxLayout()
        row_referer.addWidget(self.referer_edit, 1)
        row_referer.addWidget(self.reload_button)
        row_tools = QHBoxLayout()
        row_tools.addWidget(self.summary, 1)
        row_tools.addWidget(CaptionLabel("宽度不足", self))
        row_tools.addWidget(self.min_width_edit)
        row_tools.addWidget(CaptionLabel("px 的不勾选", self))
        row_tools.addSpacing(8)
        row_tools.addWidget(self.select_all_button)
        row_tools.addWidget(self.select_none_button)
        row_bottom = QHBoxLayout()
        row_bottom.addWidget(self.status, 1)
        row_bottom.addWidget(self.cancel_button)
        row_bottom.addWidget(self.import_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel("从网页导入图片", self))
        layout.addLayout(row_url)
        layout.addLayout(row_referer)
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(row_tools)
        layout.addWidget(self.progress)
        layout.addLayout(row_bottom)

        signals = self.importer.signals
        signals.pageFetched.connect(self._on_page_fetched)
        signals.fetchFailed.connect(self._on_fetch_failed)
        signals.imageLoaded.connect(self._on_image_loaded)
        signals.imageFailed.connect(self._on_image_failed)
        self._apply_theme()
        self._update_summary()

    # -- theme --------------------------------------------------------------
    def _apply_theme(self) -> None:
        dark = isDarkTheme()
        card = "#2b2b2b" if dark else "#ffffff"
        border = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.10)"
        self.setStyleSheet(
            f"WebImportDialog {{ background: {BG_DARK if dark else BG_LIGHT}; }}"
            f"#thumbCard {{ background: {card}; border: 1px solid {border}; border-radius: 8px; }}")

    # -- actions ------------------------------------------------------------
    def current_url(self) -> str:
        return self.url_edit.text().strip()

    def parse(self) -> None:
        try:
            url = normalize_url(self.url_edit.text())
        except WebImportError as exc:
            self.status.setText(str(exc))
            return
        self.url_edit.setText(url)
        self._clear_cards()
        self.parse_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.status.setText("正在解析网页…")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.importer.fetch_page(url)

    def reload_images(self) -> None:
        """Download everything again (e.g. after changing the Referer)."""
        if not self._candidates:
            return
        for card in self._cards:
            card.reset()
        self._start_loading()

    def _start_loading(self) -> None:
        referer = self.referer_edit.text().strip() or self._page_url
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)
        self.progress.setRange(0, max(1, len(self._candidates)))
        self.progress.setValue(0)
        self.status.setText(f"正在下载 {len(self._candidates)} 张图片…")
        self.reload_button.setEnabled(False)
        self.importer.load_images(self._candidates, referer, self._folder)
        self._update_summary()

    # -- importer callbacks ---------------------------------------------------
    def _on_page_fetched(self, final_url: str, candidates: list) -> None:
        self.parse_button.setEnabled(True)
        self._page_url = final_url
        self._candidates = list(candidates)
        self._build_cards()
        if not candidates:
            self.status.setText(NO_IMAGES_HINT)
            return
        self._start_loading()

    def _on_fetch_failed(self, message: str) -> None:
        self.parse_button.setEnabled(True)
        self.status.setText(f"解析失败：{message}")

    def _on_image_loaded(self, index: int, thumb: QImage, width: int, height: int, nbytes: int,
                         path: str, suffix: str) -> None:
        if not 0 <= index < len(self._cards):
            return
        card = self._cards[index]
        card.set_loaded(thumb, width, height, nbytes, path, suffix)
        card.check.setChecked(width >= self.min_width)
        self._after_one_finished()

    def _on_image_failed(self, index: int, message: str) -> None:
        if 0 <= index < len(self._cards):
            self._cards[index].set_failed(message)
            self._after_one_finished()

    def _after_one_finished(self) -> None:
        done = sum(1 for card in self._cards if card.state != "loading")
        self.progress.setValue(done)
        failed = sum(1 for card in self._cards if card.state == "failed")
        if done == len(self._cards):
            self.reload_button.setEnabled(True)
            self.status.setText("全部加载完成" if not failed else
                                f"加载完成，{failed} 张失败（可以改 Referer 后点「重新加载图片」）")
        self._update_summary()

    # -- cards ----------------------------------------------------------------
    def _clear_cards(self) -> None:
        self._candidates = []
        for card in self._cards:
            self.grid.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self.empty_hint.setVisible(True)
        self._update_summary()

    def _build_cards(self) -> None:
        for card in self._cards:
            self.grid.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self.empty_hint.setVisible(not self._candidates)
        for index, candidate in enumerate(self._candidates):
            card = ThumbCard(index, candidate)
            card.check.toggled.connect(self._update_summary)          # once per card
            self.grid.addWidget(card, index // COLUMNS, index % COLUMNS)
            self._cards.append(card)
        self._update_summary()

    def _set_all(self, checked: bool) -> None:
        for card in self._cards:
            if card.state == "loaded":
                card.check.setChecked(checked)

    @property
    def min_width(self) -> int:
        """Images narrower than this are not ticked automatically (empty means 0)."""
        text = self.min_width_edit.text().strip()
        return int(text) if text.isdigit() else 0

    def set_min_width(self, value: int) -> None:
        self.min_width_edit.setText(str(max(0, int(value))))

    def _apply_min_width(self) -> None:
        for card in self._cards:
            if card.state == "loaded":
                card.check.setChecked(card.width_px >= self.min_width)

    def _update_summary(self, *_args) -> None:
        total = len(self._cards)
        chosen = sum(1 for card in self._cards if card.importable)
        self.summary.setText(f"已选 {chosen} / 共 {total} 张" if total else "")
        self.import_button.setEnabled(chosen > 0)

    # -- result / shutdown ------------------------------------------------------
    def accept(self) -> None:
        # Read the files now: the temp folder is removed as soon as the dialog closes.
        images, unreadable = [], 0
        for card in self._cards:
            if not card.importable:
                continue
            try:
                images.append(DownloadedImage(card.url, Path(card.path).read_bytes(), card.suffix))
            except OSError:
                unreadable += 1
        if unreadable and not images:
            self.status.setText("临时文件读取失败，请重新解析后再试")
            return
        self.images = images
        self.skipped = unreadable
        super().accept()

    def done(self, result: int) -> None:
        self.importer.cancel()
        shutil.rmtree(self._folder, ignore_errors=True)
        super().done(result)

    def closeEvent(self, event) -> None:
        self.importer.cancel()
        shutil.rmtree(self._folder, ignore_errors=True)
        super().closeEvent(event)
