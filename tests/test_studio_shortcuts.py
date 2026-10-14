"""Keyboard shortcuts: the table, the dispatch, and navigation while proofreading."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import type_text, wait_until
    from test_studio_import import ImportHarness

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]
HAVE_SAMPLES = all(p.is_file() for p in SAMPLES)

# Keys a text box uses itself; a window-wide shortcut on one of them would break typing.
TEXT_EDITING_KEYS = ["Copy", "Cut", "Paste", "Undo", "Redo", "SelectAll", "Delete", "Backspace",
                     "MoveToNextChar", "MoveToPreviousChar", "MoveToNextWord", "MoveToPreviousWord",
                     "MoveToStartOfLine", "MoveToEndOfLine", "MoveToStartOfDocument",
                     "MoveToEndOfDocument", "SelectNextChar", "SelectPreviousChar",
                     "DeleteStartOfWord", "DeleteEndOfWord"]


class ShortcutTableTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_names_and_keys_are_unique_and_valid(self):
        from manga_extract.studio.shortcuts import SHORTCUTS

        names = [s.name for s in SHORTCUTS]
        keys = [QKeySequence(s.keys).toString() for s in SHORTCUTS]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(keys)), len(keys))
        for shortcut in SHORTCUTS:
            self.assertFalse(QKeySequence(shortcut.keys).isEmpty(), shortcut.keys)
            self.assertTrue(shortcut.label)

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_every_entry_names_a_main_window_method(self):
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.shortcuts import SHORTCUTS

        for shortcut in SHORTCUTS:
            self.assertTrue(callable(getattr(MainWindow, shortcut.name, None)), shortcut.name)

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_no_shortcut_takes_a_key_that_text_boxes_need(self):
        from manga_extract.studio.shortcuts import SHORTCUTS

        taken = {QKeySequence(getattr(QKeySequence.StandardKey, name)).toString()
                 for name in TEXT_EDITING_KEYS if hasattr(QKeySequence.StandardKey, name)}
        for shortcut in SHORTCUTS:
            self.assertNotIn(QKeySequence(shortcut.keys).toString(), taken, shortcut.name)

    def test_hint_text(self):
        from manga_extract.studio.shortcuts import hint, keys_for

        self.assertEqual(keys_for("translate_current"), "Ctrl+T")
        self.assertEqual(hint("翻译本页", "translate_current"), "翻译本页（Ctrl+T）")
        self.assertEqual(hint("没有快捷键", "nope"), "没有快捷键")
        self.assertEqual(keys_for("nope"), "")


def key_of(text: str):
    """(Qt key, modifiers) of a sequence like 'Ctrl+Shift+T'."""
    combination = QKeySequence(text)[0]
    return combination.key(), combination.keyboardModifiers()


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class DispatchTests(ImportHarness):
    """Window-level handlers that open dialogs are replaced before the window is built."""

    DIALOGS = ["new_project", "open_project_dialog", "import_files_dialog", "capture_screen",
               "recognize_current", "translate_current", "translate_all", "export_chapter_images",
               "open_settings"]

    def build(self):
        from manga_extract.studio.main_window import MainWindow

        self.mocks = {}
        patches = []
        for name in self.DIALOGS:
            patcher = mock.patch.object(MainWindow, name, autospec=True)
            self.mocks[name] = patcher.start()
            patches.append(patcher)
        self.addCleanup(lambda: [p.stop() for p in patches])
        window = self.make_window(auto=True)
        window.resize(1280, 800)
        window.show()
        window.activateWindow()
        self.addCleanup(window.hide)
        for _ in range(10):
            self.app.processEvents()
        return window

    def test_every_shortcut_is_installed_with_its_key(self):
        from manga_extract.studio.shortcuts import SHORTCUTS

        window = self.build()
        self.assertEqual(set(window.shortcuts), {s.name for s in SHORTCUTS})
        for shortcut in SHORTCUTS:
            self.assertEqual(window.shortcuts[shortcut.name].key().toString(),
                             QKeySequence(shortcut.keys).toString())

    def test_pressing_each_key_runs_its_handler_once(self):
        from manga_extract.studio.shortcuts import SHORTCUTS

        window = self.build()
        window.pages.list.setFocus()
        for shortcut in SHORTCUTS:
            if shortcut.name not in self.DIALOGS:
                continue
            key, modifiers = key_of(shortcut.keys)
            QTest.keyClick(window.pages.list, key, modifiers)
            self.mocks[shortcut.name].assert_called_once()
            self.assertIs(self.mocks[shortcut.name].call_args[0][0], window)

    def test_plain_letters_trigger_nothing(self):
        window = self.build()
        window.pages.list.setFocus()
        for key in (Qt_Key("T"), Qt_Key("N"), Qt_Key("E")):
            QTest.keyClick(window.pages.list, key)
        self.assertTrue(all(not m.called for m in self.mocks.values()))


def Qt_Key(letter: str):
    from PySide6.QtCore import Qt

    return getattr(Qt.Key, f"Key_{letter}")


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class NavigationTests(ImportHarness):
    def ready(self, pages=2):
        window = self.make_window(auto=True)
        window.import_paths([str(p) for p in SAMPLES[:pages]])
        self.assertTrue(wait_until(self.app, lambda: all(
            self.project.load_page(p.id) is not None for p in self.project.pages)))
        self.ids = [p.id for p in self.project.pages]
        window.resize(1280, 800)
        window.show()
        window.activateWindow()
        self.addCleanup(window.hide)
        window.pages.select(self.ids[0])
        self.assertTrue(wait_until(self.app, lambda: window.blocks_panel.card_count() == 2))
        return window

    # -- pages --------------------------------------------------------------
    def test_next_and_previous_page_stop_at_the_ends(self):
        window = self.ready()
        window.next_page()
        self.assertEqual(window.pages.current_id(), self.ids[1])
        window.next_page()
        self.assertEqual(window.pages.current_id(), self.ids[1])             # no wrap
        window.previous_page()
        window.previous_page()
        self.assertEqual(window.pages.current_id(), self.ids[0])

    def test_with_no_page_selected_next_goes_to_the_first(self):
        window = self.ready()
        window.pages.list.clearSelection()
        window.pages.list.setCurrentItem(None)
        window.next_page()
        self.assertEqual(window.pages.current_id(), self.ids[0])

    def test_page_keys_do_nothing_without_a_project(self):
        window = self.make_window(project=False)
        window.next_page()
        window.previous_page()                                                # must not raise
        self.assertIsNone(window.project)

    def test_alt_arrows_change_page_from_anywhere(self):
        window = self.ready()
        edit = window.blocks_panel.card(0).target_edit
        edit.setFocus()
        QTest.keyClick(edit, *key_of("Alt+Right"))
        self.assertEqual(window.pages.current_id(), self.ids[1])
        QTest.keyClick(window.pages.list, *key_of("Alt+Left"))
        self.assertEqual(window.pages.current_id(), self.ids[0])

    # -- blocks --------------------------------------------------------------
    def test_next_block_selects_everywhere_and_puts_the_cursor_in_the_translation_box(self):
        window = self.ready()
        window.next_block()                                                   # nothing selected: the first
        self.assertEqual((window.canvas.view.selected_block, window.blocks_panel.selected_index), (0, 0))
        self.assertIs(self.app.focusWidget(), window.blocks_panel.card(0).target_edit)
        window.next_block()
        self.assertEqual(window.canvas.view.selected_block, 1)
        self.assertIs(self.app.focusWidget(), window.blocks_panel.card(1).target_edit)
        window.next_block()                                                   # clamped at the last
        self.assertEqual(window.canvas.view.selected_block, 1)
        window.previous_block()
        self.assertEqual(window.canvas.view.selected_block, 0)

    def test_block_keys_do_nothing_on_a_page_without_blocks(self):
        window = self.make_window(auto=False)
        window.import_paths([str(SAMPLES[0])])                                # not recognised
        window.next_block()
        window.review_and_next()                                              # must not raise
        self.assertEqual(window.blocks_panel.card_count(), 0)

    def test_alt_down_while_typing_moves_to_the_next_blocks_box(self):
        window = self.ready()
        window.next_block()
        edit = self.app.focusWidget()
        QTest.keyClick(edit, *key_of("Alt+Down"))
        self.assertIs(self.app.focusWidget(), window.blocks_panel.card(1).target_edit)
        self.assertEqual(window.canvas.view.selected_block, 1)

    # -- review and next -----------------------------------------------------------
    def test_ctrl_enter_ticks_the_block_and_moves_on(self):
        window = self.ready()
        window.next_block()                                                   # block 0
        window.review_and_next()
        self.assertTrue(window.blocks_panel.card(0).review_box.isChecked())
        self.assertEqual(window.blocks_panel.selected_index, 1)
        window._flush_saves()
        self.assertTrue(self.project.load_page(self.ids[0]).blocks[0].reviewed)

    def test_a_second_press_unticks_and_stays(self):
        window = self.ready()
        window.next_block()
        window.review_and_next()
        window.previous_block()                                               # back to block 0 (ticked)
        window.review_and_next()
        self.assertFalse(window.blocks_panel.card(0).review_box.isChecked())
        self.assertEqual(window.blocks_panel.selected_index, 0)               # did not move on

    def test_the_last_block_is_ticked_but_does_not_wrap(self):
        window = self.ready()
        window.next_block()
        window.next_block()                                                   # block 1, the last
        window.review_and_next()
        self.assertTrue(window.blocks_panel.card(1).review_box.isChecked())
        self.assertEqual(window.blocks_panel.selected_index, 1)
        self.assertIn("最后一个", window.status_left.text())

    def test_a_block_without_text_is_skipped_not_ticked(self):
        window = self.ready()
        window._data[self.ids[0]].blocks[0].set_source("")
        window.page_data_changed(self.ids[0])
        window.next_block()                                                   # block 0, empty
        window.review_and_next()
        self.assertFalse(window.blocks_panel.card(0).review_box.isChecked())
        self.assertEqual(window.blocks_panel.selected_index, 1)

    def test_with_nothing_selected_it_just_selects_the_first_block(self):
        window = self.ready()
        window.review_and_next()
        self.assertEqual(window.blocks_panel.selected_index, 0)
        self.assertFalse(window.blocks_panel.card(0).review_box.isChecked())

    def test_ctrl_enter_in_a_text_box_reviews_and_does_not_type_a_newline(self):
        window = self.ready()
        window.next_block()
        edit = window.blocks_panel.card(0).target_edit
        type_text(edit, "手写译文")
        QTest.keyClick(edit, *key_of("Ctrl+Return"))
        self.assertEqual(edit.toPlainText(), "手写译文")                      # no newline inserted
        self.assertTrue(window.blocks_panel.card(0).review_box.isChecked())
        self.assertEqual(window.canvas.view.selected_block, 1)

    # -- review all ----------------------------------------------------------------
    def test_review_all_ticks_every_text_block_of_the_page(self):
        window = self.ready()
        window.blocks_panel.review_all_button.click()                         # the real button
        data = window._data[self.ids[0]]
        texty = [block for block in data.blocks if block.has_text]
        self.assertEqual(len(texty), 2)
        self.assertTrue(all(block.reviewed for block in texty))
        self.assertTrue(window.blocks_panel.card(0).review_box.isChecked())
        self.assertTrue(window.blocks_panel.card(1).review_box.isChecked())
        self.assertEqual(window.blocks_panel.progress.text(), "已校对 2/2")

    def test_review_all_marks_the_page_reviewed_and_survives_a_save(self):
        window = self.ready()
        window.review_all_blocks()
        self.assertEqual(window.pages._status[self.ids[0]], "reviewed")
        window._flush_saves()
        page = self.project.load_page(self.ids[0])
        self.assertTrue(all(block.reviewed for block in page.blocks if block.has_text))

    def test_a_second_review_all_clears_the_page_again(self):
        window = self.ready()
        window.review_all_blocks()
        window.review_all_blocks()
        data = window._data[self.ids[0]]
        self.assertFalse(any(block.reviewed for block in data.blocks))
        self.assertFalse(window.blocks_panel.card(0).review_box.isChecked())
        self.assertEqual(window.blocks_panel.progress.text(), "已校对 0/2")
        self.assertNotEqual(window.pages._status[self.ids[0]], "reviewed")

    def test_review_all_leaves_a_block_without_text_alone(self):
        window = self.ready()
        data = window._data[self.ids[0]]
        data.blocks[0].set_source("")
        window.page_data_changed(self.ids[0])
        window.review_all_blocks()
        self.assertFalse(data.blocks[0].reviewed)
        self.assertTrue(data.blocks[1].reviewed)
        self.assertEqual(window.blocks_panel.progress.text(), "已校对 1/1")

    def test_review_all_with_nothing_to_review_reports_instead_of_ticking(self):
        window = self.ready()
        data = window._data[self.ids[0]]
        for block in data.blocks:
            block.set_source("")
        window.page_data_changed(self.ids[0])
        window.review_all_blocks()
        self.assertIn("没有可校对", window.status_left.text())
        self.assertFalse(any(block.reviewed for block in data.blocks))

    def test_review_all_does_nothing_without_a_project(self):
        window = self.make_window(project=False)
        window.review_all_blocks()                                            # must not raise
        self.assertIsNone(window.project)

    def test_the_shortcut_key_runs_the_bulk_review(self):
        window = self.ready()
        QTest.keyClick(window.pages.list, *key_of("Ctrl+Shift+Return"))
        self.assertTrue(all(block.reviewed for block in window._data[self.ids[0]].blocks
                            if block.has_text))

    # -- boxes ---------------------------------------------------------------------
    def test_ctrl_b_toggles_the_outlines_and_remembers_it(self):
        window = self.ready()
        shown = window.canvas.view._blocks[0].isVisible()
        window.toggle_boxes()
        self.assertEqual(window.canvas.view._blocks[0].isVisible(), not shown)
        self.assertEqual(self.config.show_boxes, not shown)
        QTest.keyClick(window.pages.list, *key_of("Ctrl+B"))
        self.assertEqual(window.canvas.view._blocks[0].isVisible(), shown)

    # -- hints ---------------------------------------------------------------------
    def test_menus_and_tooltips_show_the_keys(self):
        window = self.ready()
        export_first = window.export_button.menu().actions()[0].text()
        self.assertIn("Ctrl+E", export_first)
        self.assertIn("Ctrl+,", window.settings_button.toolTip())
        self.assertIn("Ctrl+Shift+S", window.act_capture.toolTip())
        translate = [a.text() for a in window.canvas.bar.translate_button.menu().actions()]
        self.assertIn("翻译本页（Ctrl+T）", translate)
        self.assertIn("翻译全部页面（Ctrl+Shift+T）", translate)


if __name__ == "__main__":
    unittest.main()
