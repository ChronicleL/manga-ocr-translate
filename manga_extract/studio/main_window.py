"""Main window of Manga Studio: welcome page + three-pane workspace."""

from __future__ import annotations

import ctypes
import itertools
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CheckableMenu,
    CommandBar,
    DropDownPushButton,
    FluentIcon as FIF,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    StrongBodyLabel,
    Theme,
    TitleLabel,
    isDarkTheme,
    qconfig,
    setTheme,
    setThemeColor,
)

from ..translation import WEB_ENGINES, TranslationConfig, TranslationError
from .blocks_panel import BlocksPanel
from .canvas import CanvasPane
from .capture_qt import ScreenCapture
from .config import (
    DETECTOR_MODEL_PATH,
    ENGINE_CONFIG_PATH,
    TRANSLATE_CONFIG_PATH,
    StudioConfig,
)
from .export import export_page_image
from .export_dialog import ExportDialog
from .imaging import qimage_to_png
from .overlay import OverlayRenderer, has_translation
from .pages_panel import PagesPanel
from .project import IMAGE_SUFFIXES, PageData, Project, ProjectError, STATUS_PENDING
from .settings_dialog import SettingsDialog, apply_translation_settings
from .shortcuts import SHORTCUTS, hint
from .ui_common import BG_DARK, BG_LIGHT, STATUS_BUSY, STATUS_QUEUED, system_accent
from .web_dialog import WebImportDialog
from .workers import EngineWorker, TranslationWorker, default_extractor_factory

APP_TITLE = "漫画汉化工作台"
OVERLAY_CACHE_PAGES = 4
_THEMES = {"auto": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}
_THEME_LABELS = {"auto": "跟随系统", "light": "浅色", "dark": "深色"}
IMAGE_FILTER = "图片 (" + " ".join(f"*{s}" for s in sorted(IMAGE_SUFFIXES)) + ");;所有文件 (*.*)"
OVERLAY_HINT_OFF = "翻译后可在原图与叠图之间切换"
OVERLAY_HINT_ON = "切换原图 / 叠图（空格）"


def _pane(parent: Optional[QWidget] = None, name: str = "pane") -> QFrame:
    """A rounded 'card' that hosts one column of the workspace."""
    frame = QFrame(parent)
    frame.setObjectName(name)
    return frame


def _has_user_work(data: Optional[PageData]) -> bool:
    """True when re-recognising would throw away translations or hand edits."""
    return data is not None and any(
        block.translation or block.reviewed or block.source_edited for block in data.blocks)


class WelcomePage(QWidget):
    """Shown when no project is open."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.new_button = PrimaryPushButton(FIF.ADD, "新建项目", self)
        self.open_button = PushButton(FIF.FOLDER, "打开项目", self)
        self._recent_box = QVBoxLayout()
        self._recent_title = StrongBodyLabel("最近项目", self)
        self._recent_title.hide()
        self._recent_buttons: List[PushButton] = []
        self.recentChosen = None  # callback(str), set by the window

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)

        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(TitleLabel(APP_TITLE, self))
        column.addWidget(BodyLabel("导入整话页面，识别、翻译、逐块校对，最后导出叠图。", self))
        column.addSpacing(14)
        column.addLayout(buttons)
        column.addSpacing(22)
        column.addWidget(self._recent_title)
        column.addLayout(self._recent_box)

        holder = QWidget(self)
        holder.setLayout(column)
        holder.setMaximumWidth(520)

        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(holder, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addStretch(1)

    def set_recent(self, folders: List[str]) -> None:
        for button in self._recent_buttons:
            self._recent_box.removeWidget(button)
            button.deleteLater()
        self._recent_buttons.clear()
        self._recent_title.setVisible(bool(folders))
        for folder in folders:
            path = Path(folder)
            button = PushButton(f"{path.name}    {path.parent}", self)
            button.setToolTip(folder)
            button.clicked.connect(lambda _checked=False, f=folder: self._choose(f))
            self._recent_box.addWidget(button)
            self._recent_buttons.append(button)

    def _choose(self, folder: str) -> None:
        if self.recentChosen is not None:
            self.recentChosen(folder)


class MainWindow(QMainWindow):
    """Command bar on top, welcome page or workspace in the middle, status line below.

    ``engine`` / ``overlay`` can be injected (tests use fakes so no model is loaded).
    """

    def __init__(self, config: StudioConfig, engine: Optional[EngineWorker] = None,
                 overlay: Optional[OverlayRenderer] = None,
                 translation_config: Optional[TranslationConfig] = None,
                 translate_config_path: Path = TRANSLATE_CONFIG_PATH,
                 capture_factory=None, web_dialog_factory=None, settings_factory=None,
                 export_dialog_factory=None,
                 engine_config_path: Path = ENGINE_CONFIG_PATH) -> None:
        super().__init__()
        self.config = config
        self.project: Optional[Project] = None
        self._engine_config_path = Path(engine_config_path)
        self._export_dialog_factory = export_dialog_factory or (
            lambda parent, project, folder: ExportDialog(parent, project, folder))
        self._settings_factory = settings_factory or (
            lambda parent, tconfig, econfig, devices: SettingsDialog(
                parent, tconfig, econfig, devices,
                style=(self.config.font_path, self.config.font_scale)))
        self._push_typeset_style()
        # Screenshot controller / web dialog are created on demand (tests pass fakes).
        self._capture_factory = capture_factory or (lambda window, accent: ScreenCapture(window, accent))
        self._web_dialog_factory = web_dialog_factory or (
            lambda parent, url: WebImportDialog(parent, initial_url=url))
        self._capture: Optional[ScreenCapture] = None
        self._translate_config_path = Path(translate_config_path)
        self.translation_config = translation_config or TranslationConfig.load(translate_config_path)

        # Per-project state.
        self._data: Dict[str, PageData] = {}
        self._versions: Dict[str, int] = {}               # bumped whenever a page's data changes
        self._overlay_cache: "OrderedDict[str, Tuple[int, QPixmap]]" = OrderedDict()
        self._in_flight: Set[str] = set()                 # pages queued or being recognised
        self._prev_status: Dict[str, str] = {}
        self._failures: List[Tuple[int, str]] = []        # recognition errors awaiting one report
        self._overlay_pref = False                        # the user's last original/overlay choice
        self._engine_ready = False

        # Translation batch in progress: job id -> (page id, force overwrite).
        self._translate_jobs: Dict[int, Tuple[str, bool]] = {}
        # Region re-detection jobs: req_id -> (page_id, block_index, bbox_tuple)
        self._region_jobs: Dict[str, Tuple[str, int, tuple]] = {}
        self._translate_total = 0
        self._translate_done = 0
        self._translate_applied = 0
        self._job_ids = itertools.count(1)
        self.translator = TranslationWorker()

        # Edits are saved shortly after the user stops typing, and the overlay is re-rendered
        # after a shorter pause; both belong to the project they were made in.
        self._dirty: Dict[str, PageData] = {}
        self._dirty_project: Optional[Project] = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._flush_saves)
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(300)
        self._overlay_timer.timeout.connect(self._sync_current_overlay)

        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)
        self.setAcceptDrops(True)

        setTheme(_THEMES.get(config.theme, Theme.AUTO))
        self._accent = QColor(system_accent())
        setThemeColor(self._accent)
        self._build_ui()
        self.canvas.view.set_accent(self._accent)
        self._apply_colors()
        qconfig.themeChanged.connect(self._on_theme_changed)   # global signal: bound method only
        self._restore_geometry()
        self._refresh_actions()
        self.welcome.set_recent(self._existing_recent())

        self.engine = engine or EngineWorker(default_extractor_factory(
            DETECTOR_MODEL_PATH, ENGINE_CONFIG_PATH, os.environ.get("HF_ENDPOINT")))
        self.overlay = overlay or OverlayRenderer(self)
        self._connect_workers()
        self._install_shortcuts()
        self.engine.start()
        QTimer.singleShot(0, self._reopen_last_project)

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Command bar row.
        self.project_label = StrongBodyLabel("", self)
        self.project_label.hide()  # shown once a project is open
        self.bar = CommandBar(self)
        self.bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.act_new = Action(FIF.ADD, "新建项目", self)
        self.act_new.setToolTip(hint("新建项目", "new_project"))
        self.act_new.triggered.connect(self.new_project)
        self.act_open = Action(FIF.FOLDER, "打开项目", self)
        self.act_open.setToolTip(hint("打开项目", "open_project_dialog"))
        self.act_open.triggered.connect(self.open_project_dialog)
        self.bar.addAction(self.act_new)
        self.bar.addAction(self.act_open)
        self.bar.addSeparator()

        self.import_button = DropDownPushButton(FIF.PHOTO, "导入", self)
        import_menu = RoundMenu(parent=self)
        act_files = Action(FIF.PHOTO, hint("图片文件…", "import_files_dialog"))
        act_files.triggered.connect(self.import_files_dialog)
        act_folder = Action(FIF.FOLDER, "文件夹…")
        act_folder.triggered.connect(self.import_folder_dialog)
        act_clipboard = Action(FIF.PASTE, "剪贴板图片（Ctrl+V）")
        act_clipboard.triggered.connect(self.import_from_clipboard)
        import_menu.addActions([act_files, act_folder, act_clipboard])
        self.import_button.setMenu(import_menu)
        self.bar.addWidget(self.import_button)

        self.act_capture = Action(FIF.CAMERA, "截图", self)
        self.act_capture.setToolTip(hint("框选屏幕上的区域（比如浏览器里的漫画页）作为新页面", "capture_screen"))
        self.act_capture.triggered.connect(self.capture_screen)
        self.bar.addAction(self.act_capture)
        self.act_web = Action(FIF.GLOBE, "网页", self)
        self.act_web.setToolTip("输入章节网址，自动找出页面里的图片")
        self.act_web.triggered.connect(self.import_from_web)
        self.bar.addAction(self.act_web)

        self.recognize_button = DropDownPushButton(FIF.PLAY, "识别", self)
        recognize_menu = CheckableMenu(parent=self)
        act_current = Action(FIF.PLAY, hint("识别当前页", "recognize_current"))
        act_current.triggered.connect(self.recognize_current)
        act_pending = Action(FIF.SYNC, "识别全部未识别的页")
        act_pending.triggered.connect(self.recognize_pending)
        recognize_menu.addActions([act_current, act_pending])
        recognize_menu.addSeparator()
        self.act_auto = Action("导入后自动识别")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(self.config.auto_recognize)
        self.act_auto.toggled.connect(self._on_auto_toggled)
        recognize_menu.addAction(self.act_auto)
        self.recognize_button.setMenu(recognize_menu)
        self.bar.addWidget(self.recognize_button)

        self.export_button = DropDownPushButton(FIF.SAVE, "导出", self)
        export_menu = RoundMenu(parent=self)
        for icon, text, handler in (
            (FIF.PHOTO, hint("整话图片…", "export_chapter_images"), self.export_chapter_images),
            (FIF.IMAGE_EXPORT, "当前页图片…", self.export_current_page),
        ):
            action = Action(icon, text)
            action.triggered.connect(lambda _checked=False, h=handler: h())
            export_menu.addAction(action)
        export_menu.addSeparator()
        for icon, text, handler in (
            (FIF.DOCUMENT, "文本（原文与译文）…", self.export_text_file),
            (FIF.SHARE, "JSON（含坐标与校对状态）…", self.export_json_file),
        ):
            action = Action(icon, text)
            action.triggered.connect(lambda _checked=False, h=handler: h())
            export_menu.addAction(action)
        self.export_button.setMenu(export_menu)
        self.bar.addWidget(self.export_button)

        self.theme_button = DropDownPushButton(FIF.PALETTE, "主题", self)
        theme_menu = RoundMenu(parent=self)
        for key, label in _THEME_LABELS.items():
            action = Action(label)
            action.triggered.connect(lambda _checked=False, k=key: self.set_theme(k))
            theme_menu.addAction(action)
        self.theme_button.setMenu(theme_menu)

        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(16, 10, 16, 6)
        bar_row.setSpacing(14)
        bar_row.addWidget(self.project_label)
        bar_row.addWidget(self.bar, 1)
        self.settings_button = PushButton(FIF.SETTING, "设置", self)
        self.settings_button.setToolTip(hint("翻译接口、翻译通道、识别性能", "open_settings"))
        self.settings_button.clicked.connect(self.open_settings)
        bar_row.addWidget(self.settings_button)
        bar_row.addWidget(self.theme_button)
        root.addLayout(bar_row)

        # Welcome page / workspace.
        self.stack = QStackedWidget(self)
        self.welcome = WelcomePage(self)
        self.welcome.new_button.clicked.connect(self.new_project)
        self.welcome.open_button.clicked.connect(self.open_project_dialog)
        self.welcome.recentChosen = self.open_project
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self._build_workspace())
        root.addWidget(self.stack, 1)

        # Status line: message | model-loading bar | backend.
        status = QWidget(self)
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(16, 4, 16, 8)
        status_row.setSpacing(12)
        self.status_left = CaptionLabel("就绪", self)
        self.engine_bar = IndeterminateProgressBar(self)
        self.engine_bar.setFixedWidth(110)
        self.status_right = CaptionLabel("正在加载识别模型…", self)
        status_row.addWidget(self.status_left, 1)
        status_row.addWidget(self.engine_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.status_right)
        root.addWidget(status)

    def _build_workspace(self) -> QWidget:
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(12, 0, 12, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, holder)
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(False)

        # Left: pages.
        left = _pane(self.splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 6)
        self.pages = PagesPanel(left)
        self.pages.pageSelected.connect(self._on_page_selected)
        self.pages.pagesDropped.connect(self.import_paths)
        self.pages.removeRequested.connect(self._on_remove_requested)
        self.pages.reprocessRequested.connect(self._on_reprocess_requested)
        self.pages.orderChanged.connect(self._on_order_changed)
        left_layout.addWidget(self.pages)

        # Centre: the zoomable canvas with its floating bar.
        centre = _pane(self.splitter, "viewerPane")
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(1, 1, 1, 1)
        self.canvas = CanvasPane(centre)
        self.canvas.view.blockClicked.connect(self._on_block_clicked)
        self.canvas.view.blockGeometryChanged.connect(self._on_block_geometry_changed)
        self.canvas.view.blockAddRequested.connect(self._on_block_add_requested)
        self.canvas.view.blockDeleteRequested.connect(self._on_block_delete_requested)
        self.canvas.view.blockRedetectRequested.connect(self._on_block_redetect_requested)
        self.canvas.overlayToggled.connect(self._on_overlay_toggled)
        self.canvas.bar.boxes_button.setChecked(self.config.show_boxes)
        self.canvas.boxesToggled.connect(self._on_boxes_toggled)
        self.canvas.view.set_boxes_visible(self.config.show_boxes)
        self.canvas.show_message("导入页面后在这里查看")
        bar = self.canvas.bar
        bar.translatePageRequested.connect(self.translate_current)
        bar.translateAllRequested.connect(self.translate_all)
        bar.translatePendingRequested.connect(self.translate_pending)
        bar.translateCancelRequested.connect(self.cancel_translation)
        bar.channelChosen.connect(self._on_channel_chosen)
        self._refresh_channels()
        centre_layout.addWidget(self.canvas)

        # Right: proofreading cards, one per text block.
        right = _pane(self.splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.blocks_panel = BlocksPanel(right)
        self.blocks_panel.sourceEdited.connect(self._on_source_edited)
        self.blocks_panel.translationEdited.connect(self._on_translation_edited)
        self.blocks_panel.reviewedToggled.connect(self._on_reviewed_toggled)
        self.blocks_panel.fontSizeEdited.connect(self._on_font_size_edited)
        self.blocks_panel.reviewAllRequested.connect(self.review_all_blocks)
        self.blocks_panel.retranslateRequested.connect(self._on_retranslate_block)
        self.blocks_panel.cardSelected.connect(self._on_card_selected)
        self.blocks_panel.show_message("导入页面并识别后，每个文本块的原文与译文会按阅读顺序显示在这里。")
        right_layout.addWidget(self.blocks_panel)
        right.setMinimumWidth(280)

        for scope in (self.pages, self.canvas):
            paste = QShortcut(QKeySequence.StandardKey.Paste, scope)
            paste.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            paste.activated.connect(self.import_from_clipboard)

        self.splitter.addWidget(left)
        self.splitter.addWidget(centre)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes(self.config.splitter_sizes or [230, 700, 340])
        layout.addWidget(self.splitter)
        return holder

    def _connect_workers(self) -> None:
        signals = self.engine.signals
        signals.loaded.connect(self._on_engine_loaded)
        signals.loadFailed.connect(self._on_engine_failed)
        signals.started.connect(self._on_recognition_started)
        signals.progress.connect(self._on_recognition_progress)
        signals.recognized.connect(self._on_recognized)
        signals.failed.connect(self._on_recognition_failed)
        signals.regionRecognized.connect(self._on_region_recognized)
        signals.regionFailed.connect(self._on_region_failed)
        self.overlay.rendered.connect(self._on_overlay_rendered)
        self.overlay.failed.connect(self._on_overlay_failed)
        self.translator.signals.translated.connect(self._on_translated)
        self.translator.signals.failed.connect(self._on_translation_failed)

    # -- shortcuts & keyboard navigation -------------------------------------------------
    def _install_shortcuts(self) -> None:
        """Bind every entry of ``shortcuts.SHORTCUTS`` to the method of the same name."""
        self.shortcuts: Dict[str, QShortcut] = {}
        for item in SHORTCUTS:
            shortcut = QShortcut(QKeySequence(item.keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(getattr(self, item.name))
            self.shortcuts[item.name] = shortcut

    def _step_page(self, delta: int) -> None:
        if self.project is None or not self.project.pages:
            return
        ids = [page.id for page in self.project.pages]
        current = self.pages.current_id()
        index = ids.index(current) if current in ids else -1
        target = 0 if index < 0 else max(0, min(len(ids) - 1, index + delta))
        if ids[target] != current:
            self.pages.select(ids[target])

    def previous_page(self) -> None:
        self._step_page(-1)

    def next_page(self) -> None:
        self._step_page(1)

    def _focus_block(self, index: int) -> None:
        """Select a block everywhere and put the cursor in its translation box."""
        self.canvas.view.select_block(index)
        self.canvas.view.reveal_block(index)
        self.blocks_panel.select(index, scroll=True)
        self._describe_block(index)
        self.blocks_panel.card(index).target_edit.setFocus()

    def _step_block(self, delta: int) -> None:
        count = self.blocks_panel.card_count()
        if not count:
            return
        current = self.blocks_panel.selected_index
        target = 0 if current < 0 else max(0, min(count - 1, current + delta))
        self._focus_block(target)

    def previous_block(self) -> None:
        self._step_block(-1)

    def next_block(self) -> None:
        self._step_block(1)

    def review_and_next(self) -> None:
        """Tick the current block as reviewed and move on (a second press un-ticks it)."""
        count = self.blocks_panel.card_count()
        if not count:
            return
        index = self.blocks_panel.selected_index
        if index < 0:
            self._focus_block(0)
            return
        data = self._data.get(self.pages.current_id())
        block = data.blocks[index] if data is not None and index < len(data.blocks) else None
        card = self.blocks_panel.card(index)
        if block is not None and block.has_text:
            card.review_box.setChecked(not card.review_box.isChecked())
            if not card.review_box.isChecked():
                return                                  # un-ticked: stay on this block
        if index == count - 1:
            self.set_status("已经是本页最后一个文本块")
            return
        self._step_block(1)

    def toggle_boxes(self) -> None:
        self.canvas.bar.boxes_button.toggle()

    # -- bulk review -------------------------------------------------------------------
    def review_all_blocks(self) -> None:
        """Tick every text block of the current page, or clear them when all are ticked.

        For the common "watched the overlay, it all looks right" pass: one click instead of
        one click per card. Ticking protects the translations from machine re-translation
        (see ``BlockData.wants_machine_translation``), so a second click clears the page again.
        """
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None:
            return
        texty = [block for block in data.blocks if block.has_text]
        if not texty:
            self.set_status("这一页没有可校对的文本块")
            return
        target = not all(block.reviewed for block in texty)
        for block in texty:
            block.reviewed = target
        self._mark_dirty(page_id)                    # written out by the usual debounce
        self.pages.update_status(page_id, data.status())
        self.blocks_panel.refresh(data.blocks)
        if target:
            self.set_status(f"已把本页 {len(texty)} 个文本块全部标记为已校对")
        else:
            self.set_status(f"已取消本页 {len(texty)} 个文本块的校对标记")

    # -- export -----------------------------------------------------------------------
    def _ready_to_export(self) -> bool:
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目。")
            return False
        if not self.project.pages:
            self._warn("项目里还没有页面", "先导入页面再导出。")
            return False
        self._flush_saves()                     # the export reads what is on disk
        return True

    def export_chapter_images(self) -> None:
        if not self._ready_to_export():
            return
        dialog = self._export_dialog_factory(self, self.project, self.config.last_export_folder)
        dialog.exec()
        folder = dialog.folder() if hasattr(dialog, "folder") else ""
        if folder:
            self.config.last_export_folder = folder

    def export_current_page(self) -> None:
        if not self._ready_to_export():
            return
        page_id = self.pages.current_id()
        if not page_id:
            self._warn("没有选中的页面", "请先在左侧选择一页。")
            return
        assert self.project is not None
        number = self.project.index_of(page_id) + 1
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出当前页图片", str(Path(self.config.last_export_folder or "") / f"{number:03d}.png"),
            "PNG 图片 (*.png);;JPG 图片 (*.jpg)")
        if not path:
            return
        try:
            export_page_image(self.project, page_id, Path(path))
        except Exception as exc:  # noqa: BLE001 - typeset / image / disk errors, all shown
            self._error("导出失败", str(exc), duration=8000)
            return
        self.config.last_export_folder = str(Path(path).parent)
        self.set_status(f"已导出到 {path}")
        InfoBar.success("导出完成", Path(path).name, duration=2500,
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    def export_text_file(self) -> None:
        self._export_file("导出文本", f"{self._project_name()}.txt", "文本文件 (*.txt)",
                          lambda path: self.project.export_text(path), "个文本块")

    def export_json_file(self) -> None:
        self._export_file("导出 JSON", f"{self._project_name()}.json", "JSON 文件 (*.json)",
                          lambda path: self.project.export_json(path), "页")

    def _project_name(self) -> str:
        return self.project.name if self.project is not None else "project"

    def _export_file(self, title: str, default_name: str, file_filter: str, writer, unit: str) -> None:
        if not self._ready_to_export():
            return
        start = str(Path(self.config.last_export_folder or "") / default_name)
        path, _filter = QFileDialog.getSaveFileName(self, title, start, file_filter)
        if not path:
            return
        try:
            count = writer(path)
        except OSError as exc:
            self._error("导出失败", str(exc), duration=8000)
            return
        self.config.last_export_folder = str(Path(path).parent)
        self.set_status(f"已导出 {count} {unit}到 {path}")
        InfoBar.success("导出完成", f"{count} {unit} → {Path(path).name}", duration=2500,
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    # -- settings ----------------------------------------------------------------
    def open_settings(self) -> None:
        # Imported here: both modules pull in onnxruntime, which the engine thread loads anyway.
        from ..detector import available_devices
        from ..runtime import EngineConfig

        current = EngineConfig.load(self._engine_config_path)
        dialog = self._settings_factory(self, self.translation_config, current, available_devices())
        if not dialog.exec():
            return
        if dialog.result_translation is not None:
            apply_translation_settings(self.translation_config, dialog.result_translation)
            try:
                self.translation_config.save(self._translate_config_path)
            except OSError as exc:
                self._warn("翻译设置没能保存", str(exc))
            self._refresh_channels()
        self._apply_typeset_style(getattr(dialog, "result_style", None))
        new_engine = dialog.result_engine
        if new_engine is not None and new_engine != current:
            try:
                new_engine.save(self._engine_config_path)
            except OSError as exc:
                self._warn("性能设置没能保存", str(exc))
            else:
                self.reload_engine()
                return
        self.set_status("设置已保存")

    def _push_typeset_style(self) -> None:
        from ..typeset import set_default_style     # imported late: pulls in cv2 (see bootstrap)

        set_default_style(self.config.font_path, self.config.font_scale)

    def _apply_typeset_style(self, style: Optional[Tuple[str, float]]) -> None:
        """Take the font / size scale from the settings dialog and redraw the overlays."""
        if style is None or style == (self.config.font_path, self.config.font_scale):
            return
        self.config.font_path, self.config.font_scale = style
        self._push_typeset_style()
        for page_id in list(self._versions):
            self._versions[page_id] += 1
        self._overlay_cache.clear()
        current = self.pages.current_id()
        if current:
            self.page_data_changed(current, save=False)

    def reload_engine(self) -> None:
        """Load the models again with the saved performance settings."""
        self._engine_ready = False
        self.engine_bar.show()
        self.set_backend_info("正在重新加载识别模型…")
        self.set_status("性能设置已保存，正在重新加载模型…")
        self.engine.reload()

    # -- theme ---------------------------------------------------------------
    def set_theme(self, key: str) -> None:
        self.config.theme = key
        setTheme(_THEMES[key])
        self._apply_colors()

    def _on_theme_changed(self, _theme) -> None:
        self._apply_colors()

    def _apply_colors(self) -> None:
        dark = isDarkTheme()
        background = BG_DARK if dark else BG_LIGHT
        card = "#2b2b2b" if dark else "#ffffff"
        viewer = "#1c1c1c" if dark else "#e9e9e9"
        border = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.08)"
        self.setStyleSheet(
            f"QMainWindow {{ background: {background}; }}"
            f"#pane {{ background: {card}; border: 1px solid {border}; border-radius: 8px; }}"
            f"#viewerPane {{ background: {viewer}; border: 1px solid {border}; border-radius: 8px; }}"
            "QSplitter::handle { background: transparent; }"
        )
        self.canvas.apply_theme(dark)
        self.blocks_panel.apply_theme(dark, self._accent)
        self._apply_titlebar_theme(dark)

    def _apply_titlebar_theme(self, dark: bool) -> None:
        """Match the native title bar to the theme (Windows 10 2004+ / 11)."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if dark else 0)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_titlebar_theme(isDarkTheme())

    # -- project lifecycle ---------------------------------------------------
    def _existing_recent(self) -> List[str]:
        return [p for p in self.config.recent_projects if (Path(p) / "project.json").is_file()]

    def _reopen_last_project(self) -> None:
        if self.project is not None:
            return                  # the user (or a caller) already opened something
        recent = self._existing_recent()
        if recent:
            self.open_project(recent[0], quiet=True)

    def new_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择（或新建）项目文件夹",
            str(Path(self.config.recent_projects[0]).parent) if self.config.recent_projects else "")
        if not folder:
            return
        path = Path(folder)
        if (path / "project.json").is_file():
            self.open_project(folder)
            return
        if any(path.iterdir()):
            box = MessageBox("文件夹不是空的",
                             f"「{path.name}」里已有其他文件。仍把它作为项目文件夹吗？\n"
                             "（只会新增 pages、data 两个子文件夹和 project.json）", self)
            box.yesButton.setText("仍然使用")
            box.cancelButton.setText("取消")
            if not box.exec():
                return
        try:
            self.project = Project.create(path)
        except ProjectError as exc:
            self._error("无法新建项目", str(exc))
            return
        self._enter_project()

    def open_project_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "打开项目文件夹",
            str(Path(self.config.recent_projects[0]).parent) if self.config.recent_projects else "")
        if folder:
            self.open_project(folder)

    def open_project(self, folder: str, quiet: bool = False) -> None:
        try:
            project = Project.open(folder)
        except ProjectError as exc:
            self.config.forget_project(folder)
            self.welcome.set_recent(self._existing_recent())
            if not quiet:
                self._error("无法打开项目", str(exc))
            return
        self.project = project
        self._enter_project()

    def _reset_project_state(self) -> None:
        """Drop everything tied to the previous project (page ids repeat across projects)."""
        self._flush_saves()                 # still the old project: see _dirty_project
        self.translator.cancel_all()
        self._translate_jobs.clear()
        self._finish_translation_ui()
        for page_id in list(self._in_flight):
            self.engine.discard(page_id)
        self._in_flight.clear()
        self._prev_status.clear()
        self._data.clear()
        self._versions.clear()
        self._overlay_cache.clear()

    def _enter_project(self) -> None:
        project = self.project
        assert project is not None
        self._reset_project_state()
        self.config.remember_project(project.folder)
        self.pages.clear()
        for page in project.pages:
            self.pages.add_page(page.id, project.image_path(page.id), project.page_status(page.id))
        self.stack.setCurrentIndex(1)
        self.project_label.setText(project.name)
        self.project_label.show()
        self.setWindowTitle(f"{project.name} - {APP_TITLE}")
        self._refresh_actions()
        if project.pages:
            self.pages.select(project.pages[0].id)
        else:
            self.canvas.show_message("拖入图片，或点击上方「导入」")
            self.set_status(f"项目「{project.name}」还没有页面")

    def _refresh_actions(self) -> None:
        has_project = self.project is not None
        self.import_button.setEnabled(has_project)
        self.recognize_button.setEnabled(has_project)
        self.act_capture.setEnabled(has_project)
        self.act_web.setEnabled(has_project)
        self.export_button.setEnabled(has_project)

    # -- importing -----------------------------------------------------------
    def import_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", IMAGE_FILTER)
        if paths:
            self.import_paths(paths)

    def import_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片所在的文件夹")
        if folder:
            self.import_paths([folder])

    def import_paths(self, paths: List[str]) -> None:
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目，再导入图片。")
            return
        added = self.project.add_image_files(paths)
        if not added:
            self._warn("没有可导入的图片", "支持 png / jpg / webp / bmp / tiff。")
            return
        self._after_import(added)

    def import_images(self, blobs: list) -> int:
        """Add in-memory images, ``(data, suffix, origin)`` each; returns how many were added."""
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目，再导入图片。")
            return 0
        try:
            added = self.project.add_image_blobs(blobs)
        except OSError as exc:
            self._error("导入失败", str(exc))
            return 0
        if added:
            self._after_import(added)
        return len(added)

    def _after_import(self, added: list) -> None:
        """Show freshly added pages, queue them for recognition, tell the user."""
        assert self.project is not None
        for entry in added:
            self.pages.add_page(entry.id, self.project.image_path(entry.id),
                                self.project.page_status(entry.id))
        self.pages.select(added[0].id)
        note = ""
        if self.config.auto_recognize:
            self.recognize_pages([entry.id for entry in added])
            note = "，已加入识别队列"
        self.set_status(f"已导入 {len(added)} 页{note}")
        InfoBar.success("导入完成", f"新增 {len(added)} 页{note}", duration=2200,
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    # -- clipboard / screenshot / web ---------------------------------------------
    def import_from_clipboard(self) -> None:
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目，再导入图片。")
            return
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                try:
                    data = qimage_to_png(image)
                except ValueError as exc:
                    self._warn("剪贴板图片无法使用", str(exc))
                    return
                self.import_images([(data, ".png", "剪贴板")])
                return
        if mime is not None and mime.hasUrls():
            files = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if files:
                self.import_paths(files)
                return
        self._warn("剪贴板里没有图片",
                   "先复制一张图片（比如用 Win+Shift+S 截图），或在资源管理器里复制图片文件。")

    def capture_screen(self) -> None:
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目，再截图。")
            return
        if self._capture is None:
            self._capture = self._capture_factory(self, self._accent)
            self._capture.captured.connect(self._on_screenshot)
            self._capture.cancelled.connect(self._on_capture_cancelled)
        if self._capture.active:
            return
        self.set_status("拖动鼠标框选要提取的区域，Esc 或右键取消")
        self._capture.start()

    def _on_screenshot(self, image) -> None:
        try:
            data = qimage_to_png(image)
        except ValueError as exc:
            self._warn("截图无法保存", str(exc))
            return
        self.import_images([(data, ".png", "截图")])

    def _on_capture_cancelled(self) -> None:
        self.set_status("已取消截图")

    def import_from_web(self) -> None:
        if self.project is None:
            self._warn("还没有项目", "请先新建或打开一个项目，再从网页导入。")
            return
        dialog = self._web_dialog_factory(self, self.config.last_url)
        accepted = dialog.exec()
        url = dialog.current_url()
        if url:
            self.config.last_url = url                   # next chapter: just edit the number
        if not accepted or not dialog.images:
            return
        count = self.import_images([(image.data, image.suffix, image.url) for image in dialog.images])
        if count and getattr(dialog, "skipped", 0):
            self._warn("有图片没能导入", f"{dialog.skipped} 张图片的临时文件读取失败，已跳过。")

    # -- page data -----------------------------------------------------------
    def _page_data(self, page_id: str) -> Optional[PageData]:
        if page_id not in self._data and self.project is not None:
            data = self.project.load_page(page_id)
            if data is not None:
                self._data[page_id] = data
        return self._data.get(page_id)

    def page_data_changed(self, page_id: str, save: bool = True) -> None:
        """Call after a page's blocks were edited (translation, source, review flag)."""
        data = self._data.get(page_id)
        if data is None or self.project is None:
            return
        self._versions[page_id] = self._versions.get(page_id, 0) + 1
        self._overlay_cache.pop(page_id, None)
        if save:
            self._dirty.pop(page_id, None)
            self.project.save_page(page_id, data)
        self.pages.update_status(page_id, data.status())
        if page_id == self.pages.current_id():
            self.canvas.view.set_blocks(data.blocks)
            self.blocks_panel.refresh(data.blocks)
            self._sync_overlay(page_id)

    # -- page events ---------------------------------------------------------
    def _on_page_selected(self, page_id: str) -> None:
        if self.project is None or not page_id:
            return
        path = self.project.image_path(page_id)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.canvas.show_message(f"找不到图片文件：\n{path.name}")
        else:
            data = self._page_data(page_id)
            self.canvas.show_page(pixmap, data.blocks if data else [])
            self._sync_overlay(page_id)
        self._refresh_blocks_panel(page_id)
        number = self.project.index_of(page_id) + 1
        self.set_status(f"第 {number}/{len(self.project.pages)} 页 · "
                        f"{self.project.entry(page_id).origin or path.name}")

    def _on_block_clicked(self, index: int) -> None:
        """A block was clicked on the canvas: highlight it there and in the card list."""
        self.canvas.view.select_block(index)
        self.blocks_panel.select(index, scroll=True)
        self._describe_block(index)

    def _on_card_selected(self, index: int) -> None:
        """A card was clicked / focused: highlight the block and bring it into view."""
        self.canvas.view.select_block(index)
        self.canvas.view.reveal_block(index)
        self._describe_block(index)

    def _describe_block(self, index: int) -> None:
        data = self._data.get(self.pages.current_id())
        if index < 0 or data is None or index >= len(data.blocks):
            return
        block = data.blocks[index]
        direction = "竖排" if block.vertical else "横排"
        self.set_status(f"文本块 #{index + 1} · {direction} · {block.source[:40] or '（空）'}")

    def _refresh_blocks_panel(self, page_id: str) -> None:
        """Cards for a recognised page, or a hint that says why there are none."""
        data = self._data.get(page_id)
        if data is not None:
            self.blocks_panel.set_blocks(data.blocks)
        elif page_id in self._in_flight:
            running = self.pages._status.get(page_id) == STATUS_BUSY
            self.blocks_panel.show_message("正在识别这一页，稍候…" if running
                                           else "已加入识别队列，轮到这一页时会开始识别。")
        else:
            self.blocks_panel.show_message(
                "这一页还没有识别。点击上方「识别」，或在左侧页面上右键选择「重新识别」。")

    def _on_remove_requested(self, page_id: str) -> None:
        if self.project is None:
            return
        number = self.project.index_of(page_id) + 1
        box = MessageBox("删除这一页？",
                         f"第 {number} 页的图片、识别结果和译文都会从项目中删除，无法恢复。", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        index = self.project.index_of(page_id)
        self.engine.discard(page_id)
        self.overlay.forget(page_id)
        self._in_flight.discard(page_id)
        for table in (self._data, self._versions, self._overlay_cache, self._prev_status,
                      self._dirty):
            table.pop(page_id, None)
        self.project.remove_page(page_id)
        self.pages.remove_page(page_id)
        if self.project.pages:
            self.pages.select(self.project.pages[min(index, len(self.project.pages) - 1)].id)
        else:
            self.canvas.show_message("拖入图片，或点击上方「导入」")
            self.set_status("项目已没有页面")

    def _on_order_changed(self, ids: List[str]) -> None:
        if self.project is None:
            return
        try:
            self.project.reorder(ids)
        except ProjectError as exc:
            self._error("排序失败", str(exc))

    # -- recognition ---------------------------------------------------------
    def recognize_pages(self, page_ids: List[str]) -> int:
        """Queue pages for recognition (pages already queued are skipped)."""
        if self.project is None:
            return 0
        queued = 0
        for page_id in page_ids:
            if page_id in self._in_flight:
                continue
            self._prev_status[page_id] = self.project.page_status(page_id)
            self._in_flight.add(page_id)
            self.pages.update_status(page_id, STATUS_QUEUED)
            self.engine.submit(page_id, self.project.image_path(page_id))
            queued += 1
            if page_id == self.pages.current_id() and page_id not in self._data:
                self._refresh_blocks_panel(page_id)
        return queued

    def _confirm_overwrite(self, page_id: str) -> bool:
        """Ask before a re-recognition discards translations or hand edits."""
        if not _has_user_work(self._page_data(page_id)):
            return True
        box = MessageBox("重新识别会覆盖已有内容",
                         "这一页已有译文或手动修改，重新识别后它们会被清空。继续吗？", self)
        box.yesButton.setText("重新识别")
        box.cancelButton.setText("取消")
        return bool(box.exec())

    def recognize_current(self) -> None:
        page_id = self.pages.current_id()
        if page_id:
            self._on_reprocess_requested(page_id)
        else:
            self._warn("没有选中的页面", "请先在左侧选择一页。")

    def _on_reprocess_requested(self, page_id: str) -> None:
        if page_id in self._in_flight:
            self.set_status("这一页已经在识别队列里")
            return
        if not self._confirm_overwrite(page_id):
            return
        self.recognize_pages([page_id])
        self.set_status("已加入识别队列")

    def recognize_pending(self) -> None:
        if self.project is None:
            return
        ids = [page.id for page in self.project.pages
               if page.id not in self._in_flight
               and self.project.page_status(page.id) == STATUS_PENDING]
        if not ids:
            self.set_status("没有需要识别的页面")
            return
        self.recognize_pages(ids)
        self.set_status(f"已将 {len(ids)} 页加入识别队列")

    def _on_auto_toggled(self, checked: bool) -> None:
        self.config.auto_recognize = checked

    def _on_engine_loaded(self, info: str) -> None:
        self._engine_ready = True
        self.engine_bar.hide()
        self.set_backend_info(info)
        if not self._in_flight:
            self.set_status("识别引擎就绪")

    def _on_engine_failed(self, message: str) -> None:
        self.engine_bar.hide()
        self.set_backend_info("识别引擎不可用")
        self._error("识别引擎加载失败", message.split("\n", 1)[0], duration=12000)

    def _on_recognition_started(self, page_id: str) -> None:
        if page_id in self._in_flight:
            self.pages.update_status(page_id, STATUS_BUSY)
            if page_id == self.pages.current_id() and page_id not in self._data:
                self._refresh_blocks_panel(page_id)

    def _on_recognition_progress(self, page_id: str, stage: str, done: int, total: int) -> None:
        if self.project is None or page_id not in self._in_flight:
            return
        number = self.project.index_of(page_id) + 1
        if stage == "detect":
            detail = f"检测到 {done} 个文本块、{total} 行" if total else "未发现文字"
        else:
            detail = f"OCR {done}/{total} 行"
        waiting = self.engine.pending_count()
        tail = f" · 还有 {waiting} 页排队" if waiting else ""
        self.set_status(f"识别第 {number} 页 · {detail}{tail}")

    def _on_recognized(self, page_id: str, data: PageData) -> None:
        if self.project is None or page_id not in self._in_flight:
            return                          # deleted, or belongs to a previous project
        self._in_flight.discard(page_id)
        self._prev_status.pop(page_id, None)
        self._data[page_id] = data
        self._versions[page_id] = self._versions.get(page_id, 0) + 1
        self._overlay_cache.pop(page_id, None)
        self._dirty.pop(page_id, None)
        self.project.save_page(page_id, data)
        self.pages.update_status(page_id, data.status())
        number = self.project.index_of(page_id) + 1
        if page_id == self.pages.current_id():
            self.canvas.view.set_blocks(data.blocks)
            self.blocks_panel.set_blocks(data.blocks)
            self._sync_overlay(page_id)
        if self._in_flight:
            self.set_status(f"第 {number} 页识别完成 · {len(data.blocks)} 个文本块")
        else:
            self.set_status(f"识别完成 · 第 {number} 页 {len(data.blocks)} 个文本块")

    def _on_recognition_failed(self, page_id: str, message: str) -> None:
        if page_id not in self._in_flight:
            return
        self._in_flight.discard(page_id)
        previous = self._prev_status.pop(page_id, STATUS_PENDING)
        self.pages.update_status(page_id, previous)
        if page_id == self.pages.current_id():
            self._refresh_blocks_panel(page_id)
        number = self.project.index_of(page_id) + 1 if self.project else 0
        # Many pages can fail at once (e.g. the engine never loaded): report them together.
        self._failures.append((number, message.split("\n", 1)[0]))
        if len(self._failures) == 1:
            QTimer.singleShot(250, self._flush_failures)

    def _flush_failures(self) -> None:
        failures, self._failures = self._failures, []
        if not failures:
            return
        number, reason = failures[0]
        if len(failures) == 1:
            self._error(f"第 {number} 页识别失败", reason, duration=8000)
        else:
            self._error(f"{len(failures)} 页识别失败", f"例如第 {number} 页：{reason}",
                        duration=8000)

    # -- editing in the proofreading column ------------------------------------
    def _edit_target(self, index: int):
        """``(page id, data, block)`` for the card being edited, or None."""
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None or not 0 <= index < len(data.blocks):
            return None
        return page_id, data, data.blocks[index]

    def _on_source_edited(self, index: int, text: str) -> None:
        target = self._edit_target(index)
        if target is None:
            return
        page_id, data, block = target
        if block.set_source(text):
            self._mark_dirty(page_id)
            self.pages.update_status(page_id, data.status())
            self.blocks_panel.card(index).update_controls(block)   # re-translate needs text

    def _on_translation_edited(self, index: int, text: str) -> None:
        target = self._edit_target(index)
        if target is None:
            return
        page_id, data, block = target
        if block.edit_translation(text):
            self._versions[page_id] = self._versions.get(page_id, 0) + 1
            self._overlay_cache.pop(page_id, None)
            self._mark_dirty(page_id)
            self.pages.update_status(page_id, data.status())
            self._overlay_timer.start()                  # re-render once typing pauses

    # -- box editing (canvas signals) ------------------------------------------
    def _on_block_geometry_changed(self, index: int,
                                   x1: int, y1: int, x2: int, y2: int) -> None:
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None:
            return
        if data.set_bbox(index, [x1, y1, x2, y2]):
            self.page_data_changed(page_id)

    def _on_block_add_requested(self, x1: int, y1: int, x2: int, y2: int) -> None:
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None:
            return
        idx = data.add_block([x1, y1, x2, y2])
        self.page_data_changed(page_id)
        self.canvas.view.select_block(idx)
        self.blocks_panel.select(idx)

    def _on_block_delete_requested(self, index: int) -> None:
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None or not 0 <= index < len(data.blocks):
            return
        data.remove_block(index)
        self.page_data_changed(page_id)

    def _on_block_redetect_requested(self, index: int) -> None:
        page_id = self.pages.current_id()
        data = self._data.get(page_id)
        if data is None or not 0 <= index < len(data.blocks):
            return
        block = data.blocks[index]
        if block.reviewed or block.translation_edited or block.source_edited:
            dlg = MessageBox("重新检测",
                             f"#{index + 1} 已有人工改动，重新检测会覆盖原文识别结果。继续？", self)
            dlg.yesButton.setText("继续")
            dlg.cancelButton.setText("取消")
            if not dlg.exec():
                return
        if not self._engine_ready:
            self._warn("识别引擎未就绪", "模型正在加载，请稍候再试。")
            return
        import uuid as _uuid
        req_id = _uuid.uuid4().hex
        self._region_jobs[req_id] = (page_id, index, tuple(block.bbox))
        self.engine.submit_region(page_id, str(self.project.image_path(page_id)),
                                  block.bbox, req_id)
        self.set_status(f"正在重新检测 #{index + 1}…")

    def _on_region_recognized(self, page_id: str, req_id: str, extracted) -> None:
        job = self._region_jobs.pop(req_id, None)
        if job is None:
            return
        job_page_id, job_index, job_bbox = job
        if job_page_id != page_id:
            return
        data = self._data.get(page_id)
        if data is None or not 0 <= job_index < len(data.blocks):
            return
        block = data.blocks[job_index]
        if tuple(block.bbox) != job_bbox:
            return                          # the user moved the box while we were working
        if extracted is None or not extracted.text.strip():
            self._warn("重新检测", f"#{job_index + 1} 未能识别出文字。")
            return
        block.bbox     = [int(v) for v in extracted.xyxy]
        block.vertical = extracted.vertical
        block.lines    = [{"polygon": ln.polygon, "text": ln.text} for ln in extracted.lines]
        block.source   = extracted.text.strip()
        block.source_edited = False         # it came from OCR, not user edits
        self.page_data_changed(page_id)
        self.set_status(f"#{job_index + 1} 重新检测完成")

    def _on_region_failed(self, page_id: str, req_id: str, message: str) -> None:
        self._region_jobs.pop(req_id, None)
        self._warn("重新检测失败", message)

    def _on_font_size_edited(self, index: int, size: int) -> None:
        target = self._edit_target(index)
        if target is None:
            return
        page_id, _data, block = target
        if block.font_size != size:
            block.font_size = size
            self._versions[page_id] = self._versions.get(page_id, 0) + 1
            self._overlay_cache.pop(page_id, None)
            self._mark_dirty(page_id)
            self._overlay_timer.start()

    def _on_reviewed_toggled(self, index: int, checked: bool) -> None:
        target = self._edit_target(index)
        if target is None:
            return
        page_id, data, block = target
        block.reviewed = checked
        self._mark_dirty(page_id)
        self.pages.update_status(page_id, data.status())
        self.blocks_panel.card(index).update_controls(block)

    def _mark_dirty(self, page_id: str) -> None:
        """Remember an edited page; it is written shortly after the user stops typing."""
        if self.project is None or page_id not in self._data:
            return
        if self._dirty and self._dirty_project is not self.project:
            self._flush_saves()                          # leftovers of another project
        self._dirty[page_id] = self._data[page_id]
        self._dirty_project = self.project
        self._save_timer.start()

    def _flush_saves(self) -> None:
        """Write every edited page now (also on page/project switch and on close)."""
        self._save_timer.stop()
        project, pending, self._dirty = self._dirty_project, self._dirty, {}
        if project is None:
            return
        for page_id, data in pending.items():
            try:
                project.save_page(page_id, data)
            except (OSError, ProjectError) as exc:
                self._error("保存失败", f"页面 {page_id}：{exc}", duration=8000)

    def _sync_current_overlay(self) -> None:
        page_id = self.pages.current_id()
        if page_id and self.project is not None:
            self._sync_overlay(page_id)

    # -- translation ----------------------------------------------------------
    def _build_translator(self):
        try:
            return self.translation_config.build_translator()
        except TranslationError as exc:
            self._warn("翻译未配置",
                       f"{exc}。翻译设置页会在后续版本加入；现在可以改用「普通」通道，"
                       "或沿用旧版悬浮窗里保存的 API 配置。")
            return None

    def translate_current(self) -> None:
        page_id = self.pages.current_id()
        if not page_id:
            self._warn("没有选中的页面", "请先在左侧选择一页。")
            return
        self._translate_pages([page_id])

    def translate_all(self) -> None:
        if self.project is not None:
            self._translate_pages([page.id for page in self.project.pages])

    def translate_pending(self) -> None:
        if self.project is None:
            return
        ids = []
        for page in self.project.pages:
            data = self._page_data(page.id)
            if data is not None and not data.fully_translated():
                ids.append(page.id)
        self._translate_pages(ids, only_missing=True)

    def _translate_pages(self, page_ids: List[str], only_missing: bool = False) -> None:
        if self.project is None:
            return
        if self._translate_jobs:
            self.set_status("正在翻译，请等待完成，或先取消")
            return
        jobs, unrecognized = [], 0
        for page_id in page_ids:
            data = self._page_data(page_id)
            if data is None:
                unrecognized += 1
                continue
            items = data.machine_items(only_missing)
            if items:
                jobs.append((page_id, items))
        note = f"（{unrecognized} 页还没有识别，已跳过）" if unrecognized else ""
        if not jobs:
            self.set_status(f"没有需要翻译的内容{note}。已校对或手改过的译文会被保留。")
            return
        translator = self._build_translator()
        if translator is None:
            return
        self._begin_translation(jobs, translator, force=False)
        if note:
            self.set_status(f"翻译中…{note}")

    def _on_retranslate_block(self, index: int) -> None:
        target = self._edit_target(index)
        if target is None:
            return
        page_id, _data, block = target
        if self._translate_jobs:
            self.set_status("正在翻译，请等待完成，或先取消")
            return
        if not block.has_text:
            return
        if block.reviewed:                   # the button is disabled too; this is the real guard
            self.set_status("这一块已标记为已校对，取消勾选后才能重新翻译")
            return
        translator = self._build_translator()
        if translator is not None:
            # An explicit request on one block: it may replace a hand-typed translation.
            self._begin_translation([(page_id, [(index, block.source)])], translator, force=True)

    def _begin_translation(self, jobs: list, translator, force: bool) -> None:
        self._translate_total = len(jobs)
        self._translate_done = 0
        self._translate_applied = 0
        for page_id, items in jobs:
            job_id = next(self._job_ids)
            self._translate_jobs[job_id] = (page_id, force)
            self.translator.submit(job_id, page_id, items, translator)
        self._update_translation_ui()

    def _update_translation_ui(self) -> None:
        done, total = self._translate_done, self._translate_total
        self.canvas.bar.set_translating(True, f"翻译中 {done}/{total}")
        self.set_status(f"翻译中… 已完成 {done}/{total} 页")

    def _finish_translation_ui(self) -> None:
        self._translate_total = self._translate_done = 0
        self.canvas.bar.set_translating(False)

    def _on_translated(self, job_id: int, page_id: str, items: list) -> None:
        job = self._translate_jobs.pop(job_id, None)
        if job is None or self.project is None:
            return                          # cancelled, or from a project that was closed
        _page, force = job
        data = self._page_data(page_id) if page_id in {p.id for p in self.project.pages} else None
        changed = False
        for index, source, text in items:
            if data is None or not text or not 0 <= index < len(data.blocks):
                continue
            block = data.blocks[index]
            # The page may have changed while the request was out: re-check every block.
            if block.source != source or not block.wants_machine_translation(force):
                continue
            if block.apply_machine_translation(text):
                changed = True
                self._translate_applied += 1
        if changed:
            self.page_data_changed(page_id)
        self._translate_done += 1
        if self._translate_jobs:
            self._update_translation_ui()
        else:
            total = self._translate_total
            self._finish_translation_ui()
            self.set_status(f"翻译完成：{total} 页，更新了 {self._translate_applied} 个文本块")

    def _on_translation_failed(self, job_id: int, page_id: str, message: str) -> None:
        if job_id not in self._translate_jobs:
            return
        done, total = self._translate_done, self._translate_total
        self.translator.cancel_all()
        self._translate_jobs.clear()
        self._finish_translation_ui()
        progress = f"（已完成 {done}/{total} 页）" if done else ""
        self._error("翻译失败", f"{message.splitlines()[0] if message else '未知错误'}{progress}",
                    duration=10000)
        self.set_status("翻译已中断")

    def cancel_translation(self) -> None:
        if not self._translate_jobs:
            return
        done, total = self._translate_done, self._translate_total
        self.translator.cancel_all()
        self._translate_jobs.clear()
        self._finish_translation_ui()
        self.set_status(f"已取消翻译（已完成 {done}/{total} 页）")

    # -- translation channel ---------------------------------------------------
    def _channel_key(self) -> str:
        config = self.translation_config
        if config.mode == "ai":
            for index, profile in enumerate(config.ai_profiles):
                if profile is config.ai:
                    return f"ai:{index}"
        return f"web:{config.web_engine}"

    def _translation_channels(self) -> list:
        channels = [(f"web:{code}", f"普通 · {label}", True, "")
                    for label, code in WEB_ENGINES.items()]
        for index, profile in enumerate(self.translation_config.ai_profiles):
            ready = bool(profile.api_key and profile.model)
            tip = "" if ready else "需要先填写 API Key 和模型（翻译设置页后续提供，可沿用旧版悬浮窗里的配置）"
            channels.append((f"ai:{index}", f"AI · {profile.name}", ready, tip))
        return channels

    def _refresh_channels(self) -> None:
        self.canvas.bar.set_channels(self._translation_channels(), self._channel_key())

    def _on_channel_chosen(self, key: str) -> None:
        config = self.translation_config
        kind, _, value = key.partition(":")
        if kind == "web" and value in WEB_ENGINES.values():
            config.mode, config.web_engine = "web", value
        elif kind == "ai" and value.isdigit() and int(value) < len(config.ai_profiles):
            config.mode, config.ai = "ai", config.ai_profiles[int(value)]
        else:
            return
        try:
            config.save(self._translate_config_path)
        except OSError as exc:
            self._warn("翻译通道没能保存", str(exc))
        self._refresh_channels()
        label = next((text for k, text, _enabled, _tip in self._translation_channels() if k == key), key)
        self.set_status(f"翻译通道：{label}")

    # -- overlay (original <-> translated) -----------------------------------
    def _sync_overlay(self, page_id: str) -> None:
        """Bring the canvas and the floating bar in line with the current page's overlay."""
        view, bar = self.canvas.view, self.canvas.bar
        data = self._data.get(page_id)
        if not has_translation(data):
            view.clear_overlay()
            view.show_overlay(False)
            bar.set_overlay_state(False, OVERLAY_HINT_OFF)
            return
        version = self._versions.get(page_id, 0)
        cached = self._overlay_cache.get(page_id)
        if cached is not None and cached[0] == version:
            self._overlay_cache.move_to_end(page_id)
            view.set_overlay(cached[1])
            self._apply_overlay_pref()
            return
        view.clear_overlay()
        view.show_overlay(False)
        bar.set_overlay_state(False, "正在生成叠图…")
        assert data is not None and self.project is not None
        self.overlay.request(page_id, version, self.project.image_path(page_id), data)

    def _apply_overlay_pref(self) -> None:
        self.canvas.bar.set_overlay_state(True, OVERLAY_HINT_ON, checked=self._overlay_pref)
        self.canvas.view.show_overlay(self._overlay_pref)

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._overlay_pref = checked
        self.canvas.view.show_overlay(checked)

    def _on_boxes_toggled(self, visible: bool) -> None:
        self.config.show_boxes = visible

    def _on_overlay_rendered(self, page_id: str, version: int, image) -> None:
        if self.project is None or version != self._versions.get(page_id, 0):
            return                          # stale: the page changed again meanwhile
        pixmap = QPixmap.fromImage(image)
        self._overlay_cache[page_id] = (version, pixmap)
        self._overlay_cache.move_to_end(page_id)
        while len(self._overlay_cache) > OVERLAY_CACHE_PAGES:
            self._overlay_cache.popitem(last=False)
        if page_id == self.pages.current_id():
            self.canvas.view.set_overlay(pixmap)
            self._apply_overlay_pref()

    def _on_overlay_failed(self, page_id: str, version: int, message: str) -> None:
        if version != self._versions.get(page_id, 0) or page_id != self.pages.current_id():
            return
        self.canvas.bar.set_overlay_state(False, "叠图生成失败")
        self._warn("叠图生成失败", message)

    # -- drag & drop onto the window ------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            event.acceptProposedAction()
            self.import_paths(files)

    # -- feedback ------------------------------------------------------------
    def set_status(self, text: str) -> None:
        self.status_left.setText(text)

    def set_backend_info(self, text: str) -> None:
        self.status_right.setText(text)

    def _warn(self, title: str, content: str) -> None:
        InfoBar.warning(title, content, duration=3500,
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _error(self, title: str, content: str, duration: int = 5000) -> None:
        InfoBar.error(title, content, duration=duration,
                      position=InfoBarPosition.TOP_RIGHT, parent=self)

    # -- shutdown ------------------------------------------------------------
    def _restore_geometry(self) -> None:
        if self.config.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.config.window_geometry.encode()))

    def closeEvent(self, event) -> None:
        self.config.window_geometry = bytes(self.saveGeometry().toBase64().data()).decode()
        self.config.splitter_sizes = [int(s) for s in self.splitter.sizes()]
        try:
            self.config.save()
        except OSError:
            pass
        self._overlay_timer.stop()
        self._flush_saves()
        self.translator.stop()
        self.overlay.shutdown()
        super().closeEvent(event)
