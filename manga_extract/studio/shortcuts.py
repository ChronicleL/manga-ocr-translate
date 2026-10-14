"""Keyboard shortcuts of the studio window, in one table.

The window installs them from here, and menu items / tooltips take their hint text from
here too, so what is shown can never drift from what is bound.

Window-wide shortcuts avoid the standard text-editing keys, so typing in a proofreading box is
never hijacked. (Ctrl+Enter is not one of those keys, which is what lets it work while typing.)
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple


class Shortcut(NamedTuple):
    name: str          # also the MainWindow method that runs
    keys: str          # QKeySequence text
    label: str         # what it does, in the window's language


SHORTCUTS: List[Shortcut] = [
    Shortcut("new_project", "Ctrl+N", "新建项目"),
    Shortcut("open_project_dialog", "Ctrl+O", "打开项目"),
    Shortcut("import_files_dialog", "Ctrl+I", "导入图片文件"),
    Shortcut("capture_screen", "Ctrl+Shift+S", "截图"),
    Shortcut("recognize_current", "F5", "识别当前页"),
    Shortcut("translate_current", "Ctrl+T", "翻译本页"),
    Shortcut("translate_all", "Ctrl+Shift+T", "翻译全部页面"),
    Shortcut("export_chapter_images", "Ctrl+E", "导出整话图片"),
    Shortcut("open_settings", "Ctrl+,", "设置"),
    Shortcut("previous_page", "Alt+Left", "上一页"),
    Shortcut("next_page", "Alt+Right", "下一页"),
    Shortcut("previous_block", "Alt+Up", "上一个文本块（光标进入译文框）"),
    Shortcut("next_block", "Alt+Down", "下一个文本块（光标进入译文框）"),
    Shortcut("review_and_next", "Ctrl+Return", "标记当前块已校对并跳到下一块"),
    Shortcut("review_all_blocks", "Ctrl+Shift+Return", "把本页所有文本块标记为已校对（再按一次取消）"),
    Shortcut("toggle_boxes", "Ctrl+B", "显示 / 隐藏文本块框"),
]

_BY_NAME: Dict[str, Shortcut] = {item.name: item for item in SHORTCUTS}


def keys_for(name: str) -> str:
    """The key text of a shortcut (``""`` for an unknown name)."""
    item = _BY_NAME.get(name)
    return item.keys if item else ""


def hint(text: str, name: str) -> str:
    """``"翻译本页"`` -> ``"翻译本页（Ctrl+T）"``."""
    keys = keys_for(name)
    return f"{text}（{keys}）" if keys else text
