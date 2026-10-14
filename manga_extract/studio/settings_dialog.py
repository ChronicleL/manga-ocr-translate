"""Translation and performance settings.

The dialog edits a *copy* of the configuration, so "取消" leaves everything untouched; the
caller applies the result (``apply_translation_settings``) only after "保存".
"""

from __future__ import annotations

import copy
import threading
from dataclasses import replace
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    EditableComboBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBox,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SmoothScrollArea,
    StrongBodyLabel,
    TextEdit,
    ToolTipFilter,
    TransparentToolButton,
    isDarkTheme,
)

from ..runtime import DEVICE_LABELS, MAX_OCR_BATCH, OCR_BATCH_CHOICES, EngineConfig
from ..translation import TARGET_LANGUAGES, WEB_ENGINES, TranslationConfig
from .settings_text import (
    AI_MODEL_PRESETS,
    DEVICE_HELP,
    PERF_HELP,
    TEMPERATURE_HELP,
    TEST_SENTENCE,
    THINKING_HELP,
    parse_temperature,
    parse_threads,
)
from .ui_common import BG_DARK, BG_LIGHT

# builder(config) -> translator with .translate(list[str]); replaced in tests.
TranslatorBuilder = Callable[[TranslationConfig], object]


def apply_translation_settings(target: TranslationConfig, edited: TranslationConfig) -> None:
    """Copy the edited settings into ``target`` *in place* (the window keeps one object)."""
    target.web_engine = edited.web_engine
    target.target_lang = edited.target_lang
    target.ai_profiles = edited.ai_profiles
    target.ai = edited.ai
    target.ensure_profiles()


def _label_for(mapping: dict, code: str) -> str:
    for label, value in mapping.items():
        if value == code:
            return label
    return next(iter(mapping))


class _TestSignals(QObject):
    finished = Signal(str, str)         # channel, text to show


class SettingsDialog(QDialog):
    """After ``exec()`` is accepted: ``result_translation`` and ``result_engine`` are set."""

    def __init__(self, parent: Optional[QWidget], translation_config: TranslationConfig,
                 engine_config: EngineConfig, devices: List[str],
                 translator_builder: Optional[TranslatorBuilder] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(660, 780)
        self.setMinimumSize(560, 460)

        self.config = copy.deepcopy(translation_config)          # edited copy (identity kept)
        self.config.ensure_profiles()
        self.engine = engine_config
        self.result_translation: Optional[TranslationConfig] = None
        self.result_engine: Optional[EngineConfig] = None
        self._builder = translator_builder or (lambda cfg: cfg.build_translator())
        self._index = 0
        self._loading = False
        self._running = set()
        self._signals = _TestSignals()
        self._signals.finished.connect(self._on_test_finished)

        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(4, 4, 12, 4)
        column.setSpacing(12)
        column.addWidget(self._build_web_card())
        column.addWidget(self._build_ai_card())
        column.addWidget(self._build_perf_card(devices))
        column.addStretch(1)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        scroll.setStyleSheet("SmoothScrollArea { background: transparent; border: none; }")
        holder.setStyleSheet("background: transparent;")

        self.cancel_button = PushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = PrimaryPushButton("保存", self)
        self.save_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)

        self._load_profile(self._active_index())
        self._apply_theme()

    # -- construction ---------------------------------------------------------
    def _card(self, title: str) -> tuple:
        frame = QFrame(self)
        frame.setObjectName("settingsCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(8)
        box.addWidget(StrongBodyLabel(title, frame))
        return frame, box

    @staticmethod
    def _row(label: str, *widgets: QWidget, stretch: int = 0, top: bool = False) -> QHBoxLayout:
        """A captioned row; ``widgets[stretch]`` takes the spare width (-1: none, pad right).

        ``top`` aligns the caption with the top edge, for tall widgets such as a text box.
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = BodyLabel(label)
        caption.setFixedWidth(84)
        if top:
            caption.setContentsMargins(0, 6, 0, 0)
        row.addWidget(caption, 0, Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag(0))
        for index, widget in enumerate(widgets):
            row.addWidget(widget, 1 if index == stretch else 0)
        if stretch < 0:
            row.addStretch(1)
        return row

    @staticmethod
    def _help(widget: QWidget, text: str) -> None:
        widget.setToolTip(text)
        widget.installEventFilter(ToolTipFilter(widget, 300))

    def _build_web_card(self) -> QFrame:
        frame, box = self._card("普通翻译（网页通道）")
        self.engine_box = ComboBox(frame)
        self.engine_box.addItems(list(WEB_ENGINES.keys()))
        self.engine_box.setCurrentText(_label_for(WEB_ENGINES, self.config.web_engine))
        self.lang_box = ComboBox(frame)
        self.lang_box.addItems(list(TARGET_LANGUAGES.keys()))
        self.lang_box.setCurrentText(_label_for(TARGET_LANGUAGES, self.config.target_lang))
        self.web_test_button = PushButton("测试机翻", frame)
        self.web_test_button.clicked.connect(lambda: self.run_test("web"))
        self.web_result = CaptionLabel("", frame)
        self.web_result.setWordWrap(True)
        self.web_result.hide()
        box.addLayout(self._row("翻译引擎", self.engine_box, stretch=-1))
        box.addLayout(self._row("目标语言", self.lang_box, stretch=-1))
        test_row = QHBoxLayout()
        test_row.addWidget(self.web_test_button)
        test_row.addStretch(1)
        box.addLayout(test_row)
        box.addWidget(self.web_result)
        return frame

    def _build_ai_card(self) -> QFrame:
        frame, box = self._card("AI 翻译（OpenAI 兼容接口）")
        self.profile_box = ComboBox(frame)
        self.add_button = PushButton("新增", frame)
        self.delete_button = PushButton("删除", frame)
        self.add_button.clicked.connect(self.add_profile)
        self.delete_button.clicked.connect(self.delete_profile)
        self.name_edit = LineEdit(frame)
        self.base_edit = LineEdit(frame)
        self.base_edit.setPlaceholderText("https://api.deepseek.com/v1")
        self.key_edit = PasswordLineEdit(frame)
        self.model_box = EditableComboBox(frame)
        self.model_box.addItems(AI_MODEL_PRESETS)
        self.temp_edit = LineEdit(frame)
        self.temp_edit.setFixedWidth(84)
        self.temp_help = TransparentToolButton(FIF.HELP, frame)
        self._help(self.temp_help, TEMPERATURE_HELP)
        self.thinking_check = CheckBox("关闭思考模式（更快）", frame)
        self._help(self.thinking_check, THINKING_HELP)
        self.prompt_edit = TextEdit(frame)
        self.prompt_edit.setAcceptRichText(False)
        self.prompt_edit.setFixedHeight(84)
        self.prompt_edit.setPlaceholderText("追加给 AI 的风格要求，例如「用轻松吐槽的语气，保留拟声词」")
        self.ai_test_button = PushButton("测试 AI", frame)
        self.ai_test_button.clicked.connect(lambda: self.run_test("ai"))
        self.ai_result = CaptionLabel("", frame)
        self.ai_result.setWordWrap(True)
        self.ai_result.hide()

        profile_row = self._row("接口配置", self.profile_box, self.add_button, self.delete_button)
        box.addLayout(profile_row)
        box.addLayout(self._row("名称", self.name_edit))
        box.addLayout(self._row("接口地址", self.base_edit))
        box.addLayout(self._row("API Key", self.key_edit))
        box.addLayout(self._row("模型", self.model_box))
        box.addLayout(self._row("随机性", self.temp_edit, self.temp_help, self.thinking_check,
                                stretch=-1))
        box.addLayout(self._row("提示词", self.prompt_edit, top=True))
        test_row = QHBoxLayout()
        test_row.addWidget(self.ai_test_button)
        test_row.addStretch(1)
        box.addLayout(test_row)
        box.addWidget(self.ai_result)
        self.profile_box.currentIndexChanged.connect(self._on_profile_selected)
        return frame

    def _build_perf_card(self, devices: List[str]) -> QFrame:
        frame, box = self._card("性能（保存后自动重新加载模型）")
        self.device_box = ComboBox(frame)
        for code in devices:
            self.device_box.addItem(DEVICE_LABELS.get(code, code))
        self._device_codes = list(devices)
        shown = self.engine.device if self.engine.device in devices else "auto"
        self.device_box.setCurrentIndex(devices.index(shown) if shown in devices else 0)
        self.device_help = TransparentToolButton(FIF.HELP, frame)
        self._help(self.device_help, DEVICE_HELP)
        self.threads_edit = LineEdit(frame)
        self.threads_edit.setValidator(QIntValidator(0, 256, self))
        self.threads_edit.setFixedWidth(84)
        self.threads_edit.setText(str(self.engine.threads))
        self.batch_box = ComboBox(frame)
        self.batch_box.addItems([str(n) for n in OCR_BATCH_CHOICES])
        self.batch_box.setCurrentText(str(self.engine.ocr_batch))
        hint = CaptionLabel(PERF_HELP, frame)
        hint.setWordWrap(True)
        box.addLayout(self._row("执行后端", self.device_box, self.device_help, stretch=-1))
        box.addLayout(self._row("CPU 线程", self.threads_edit, stretch=-1))
        box.addLayout(self._row("OCR 批处理", self.batch_box, stretch=-1))
        box.addWidget(hint)
        return frame

    def _apply_theme(self) -> None:
        dark = isDarkTheme()
        card = "#2b2b2b" if dark else "#ffffff"
        border = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.10)"
        self.setStyleSheet(
            f"SettingsDialog {{ background: {BG_DARK if dark else BG_LIGHT}; }}"
            f"#settingsCard {{ background: {card}; border: 1px solid {border}; border-radius: 8px; }}")

    # -- AI profiles ------------------------------------------------------------
    def _active_index(self) -> int:
        for index, profile in enumerate(self.config.ai_profiles):
            if profile is self.config.ai:
                return index
        return 0

    def profile_names(self) -> List[str]:
        return [profile.name or f"配置 {i + 1}" for i, profile in enumerate(self.config.ai_profiles)]

    def _refresh_profile_box(self, select: int) -> None:
        self._loading = True
        self.profile_box.clear()
        self.profile_box.addItems(self.profile_names())
        self.profile_box.setCurrentIndex(select)
        self._loading = False

    def _load_profile(self, index: int) -> None:
        profile = self.config.ai_profiles[index]
        self._index = index
        self._loading = True
        self.name_edit.setText(profile.name)
        self.base_edit.setText(profile.base_url)
        self.key_edit.setText(profile.api_key)
        self.model_box.setText(profile.model)
        self.temp_edit.setText(str(profile.temperature))
        self.thinking_check.setChecked(profile.disable_thinking)
        self.prompt_edit.setPlainText(profile.prompt)
        self._loading = False
        self.config.ai = profile
        self._refresh_profile_box(index)
        self.delete_button.setEnabled(len(self.config.ai_profiles) > 1)

    def store_profile(self) -> None:
        """Write the form back into the profile that is currently shown."""
        profile = self.config.ai_profiles[self._index]
        profile.name = self.name_edit.text().strip() or f"配置 {self._index + 1}"
        profile.base_url = self.base_edit.text().strip()
        profile.api_key = self.key_edit.text().strip()
        profile.model = self.model_box.text().strip()
        profile.temperature = parse_temperature(self.temp_edit.text())
        profile.prompt = self.prompt_edit.toPlainText().strip()
        profile.disable_thinking = self.thinking_check.isChecked()
        self.config.ai = profile

    def _on_profile_selected(self, index: int) -> None:
        if self._loading or index < 0 or index == self._index:
            return
        self.store_profile()
        self._load_profile(index)

    def add_profile(self) -> None:
        self.store_profile()
        current = self.config.ai_profiles[self._index]
        self.config.ai_profiles.append(
            replace(current, name=self.config.unique_profile_name(), api_key=""))
        self._load_profile(len(self.config.ai_profiles) - 1)

    def delete_profile(self) -> None:
        if len(self.config.ai_profiles) <= 1:
            return
        name = self.config.ai_profiles[self._index].name
        box = MessageBox("删除配置", f"确定删除「{name}」吗？", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        del self.config.ai_profiles[self._index]
        self._load_profile(min(self._index, len(self.config.ai_profiles) - 1))

    # -- "test" buttons -----------------------------------------------------------
    def _test_config(self, channel: str) -> TranslationConfig:
        language = TARGET_LANGUAGES.get(self.lang_box.currentText(), "zh-CN")
        if channel == "web":
            return TranslationConfig(mode="web", web_engine=WEB_ENGINES.get(
                self.engine_box.currentText(), "google"), target_lang=language)
        return TranslationConfig(mode="ai", target_lang=language, ai=replace(self.config.ai))

    def run_test(self, channel: str) -> None:
        """Translate one sentence on a thread; the two channels are independent."""
        if channel in self._running:
            return
        self.store_profile()
        self._running.add(channel)
        label = self.web_result if channel == "web" else self.ai_result
        label.setText("测试中…")
        label.setStyleSheet("")
        label.show()
        config = self._test_config(channel)

        def work() -> None:
            try:
                out = self._builder(config).translate([TEST_SENTENCE])
                text = "✓ " + (out[0] if out else "(空)")
            except Exception as exc:  # noqa: BLE001 - shown to the user
                text = f"✗ {exc}"
            self._signals.finished.emit(channel, text)

        threading.Thread(target=work, name=f"test-{channel}", daemon=True).start()

    def _on_test_finished(self, channel: str, text: str) -> None:
        self._running.discard(channel)
        label = self.web_result if channel == "web" else self.ai_result
        label.setText(text)
        label.setStyleSheet("color: #16a34a;" if text.startswith("✓") else "color: #dc2626;")

    # -- result --------------------------------------------------------------------
    def accept(self) -> None:
        self.store_profile()
        self.config.web_engine = WEB_ENGINES.get(self.engine_box.currentText(), "google")
        self.config.target_lang = TARGET_LANGUAGES.get(self.lang_box.currentText(), "zh-CN")
        self.result_translation = self.config
        code = self._device_codes[self.device_box.currentIndex()] if self._device_codes else "auto"
        try:
            batch = int(self.batch_box.currentText())
        except ValueError:
            batch = 1
        self.result_engine = EngineConfig(device=code, threads=parse_threads(self.threads_edit.text()),
                                          ocr_batch=min(MAX_OCR_BATCH, max(1, batch))).normalized()
        super().accept()
