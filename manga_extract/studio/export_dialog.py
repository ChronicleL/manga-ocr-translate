"""Dialog for exporting the whole chapter as images, with progress and cancel."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    isDarkTheme,
)

from .export import JPEG_QUALITIES, ExportOptions, ExportReport, export_chapter
from .project import Project
from .ui_common import BG_DARK, BG_LIGHT

Exporter = Callable[..., ExportReport]
MAX_LISTED_FAILURES = 4


class _Signals(QObject):
    progress = Signal(int, int)
    finished = Signal(object)                # ExportReport


class ExportDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], project: Project, initial_folder: str = "",
                 exporter: Exporter = export_chapter) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出整话图片")
        self.setModal(True)
        self.resize(560, 360)

        self.project = project
        self._exporter = exporter
        self._cancel = threading.Event()
        self._running = False
        self.report: Optional[ExportReport] = None
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)

        self.folder_edit = LineEdit(self)
        self.folder_edit.setPlaceholderText("选择保存图片的文件夹")
        self.folder_edit.setText(initial_folder)
        self.browse_button = PushButton("浏览…", self)
        self.browse_button.clicked.connect(self.browse)

        self.format_box = ComboBox(self)
        self.format_box.addItems(["PNG（无损）", "JPG（体积小）"])
        self.quality_box = ComboBox(self)
        self.quality_box.addItems([f"质量 {q}" for q in JPEG_QUALITIES])
        self.quality_box.setEnabled(False)
        self.format_box.currentIndexChanged.connect(
            lambda index: self.quality_box.setEnabled(index == 1))

        self.originals_check = CheckBox("没有译文的页面也导出（原图），保持页码完整", self)
        self.originals_check.setChecked(True)

        translated = sum(1 for page in project.pages
                         if (data := project.load_page(page.id)) is not None and data.has_translation())
        total = len(project.pages)
        self.summary = BodyLabel(f"共 {total} 页，其中 {translated} 页有译文。"
                                 "文件按页面顺序命名为 001、002……", self)
        self.summary.setWordWrap(True)

        self.progress = ProgressBar(self)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.status = CaptionLabel("", self)
        self.status.setWordWrap(True)

        self.open_button = PushButton("打开文件夹", self)
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(self.open_folder)
        self.close_button = PushButton("关闭", self)
        self.close_button.clicked.connect(self.reject)
        self.start_button = PrimaryPushButton("开始导出", self)
        self.start_button.clicked.connect(self.start)
        self.start_button.setEnabled(total > 0)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)
        format_row = QHBoxLayout()
        format_row.addWidget(BodyLabel("格式", self))
        format_row.addWidget(self.format_box)
        format_row.addWidget(self.quality_box)
        format_row.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(StrongBodyLabel("导出整话图片", self))
        layout.addLayout(folder_row)
        layout.addLayout(format_row)
        layout.addWidget(self.originals_check)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"ExportDialog {{ background: {BG_DARK if isDarkTheme() else BG_LIGHT}; }}")

    # -- actions -------------------------------------------------------------
    def folder(self) -> str:
        return self.folder_edit.text().strip()

    def options(self) -> ExportOptions:
        fmt = "jpg" if self.format_box.currentIndex() == 1 else "png"
        quality = JPEG_QUALITIES[max(0, self.quality_box.currentIndex())]
        return ExportOptions(Path(self.folder()), fmt=fmt, quality=quality,
                             include_untranslated=self.originals_check.isChecked())

    def browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择保存图片的文件夹", self.folder())
        if chosen:
            self.folder_edit.setText(chosen)

    def open_folder(self) -> None:
        if self.folder():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder()))

    def start(self) -> None:
        if self._running:
            return
        if not self.folder():
            self.status.setText("请先选择保存图片的文件夹")
            return
        options = self.options()
        self._running = True
        self._cancel.clear()
        self.report = None
        self.progress.setValue(0)
        self.status.setText("正在导出…")
        self.start_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.folder_edit.setEnabled(False)
        self.format_box.setEnabled(False)
        self.quality_box.setEnabled(False)
        self.originals_check.setEnabled(False)
        self.open_button.setVisible(False)
        self.close_button.setText("取消")

        def work() -> None:
            try:
                report = self._exporter(self.project, options,
                                        progress=self._signals.progress.emit, cancel=self._cancel)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                report = ExportReport(failed=[(0, f"{type(exc).__name__}: {exc}")])
            self._signals.finished.emit(report)

        threading.Thread(target=work, name="export", daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.status.setText(f"正在导出 {done}/{total} 页…")

    def _on_finished(self, report: ExportReport) -> None:
        self._running = False
        self.report = report
        self.close_button.setText("关闭")
        self.close_button.setEnabled(True)               # reject() disables it while cancelling
        self.start_button.setText("再次导出")
        self.start_button.setEnabled(True)
        for widget in (self.browse_button, self.folder_edit, self.format_box, self.originals_check):
            widget.setEnabled(True)
        self.quality_box.setEnabled(self.format_box.currentIndex() == 1)
        self.open_button.setVisible(bool(report.written))
        self.status.setText(self.describe(report))

    @staticmethod
    def describe(report: ExportReport) -> str:
        parts = [f"已导出 {len(report.written)} 页"]
        if report.skipped:
            parts.append(f"跳过 {report.skipped} 页（没有译文）")
        if report.cancelled:
            parts.append("已取消")
        text = "，".join(parts) + "。"
        if report.failed:
            lines = [f"第 {position} 页：{message}" if position else message
                     for position, message in report.failed[:MAX_LISTED_FAILURES]]
            more = len(report.failed) - MAX_LISTED_FAILURES
            if more > 0:
                lines.append(f"…另有 {more} 页失败")
            text += f"\n{len(report.failed)} 页失败：\n" + "\n".join(lines)
        return text

    # -- closing ---------------------------------------------------------------
    def reject(self) -> None:
        """While exporting the button reads 取消: stop at the next page instead of closing."""
        if self._running:
            self._cancel.set()
            self.status.setText("正在取消…")
            self.close_button.setEnabled(False)
            return
        super().reject()

    def done(self, result: int) -> None:
        self._cancel.set()
        super().done(result)
