"""BlocksPanel / BlockCard: cards, editing signals, selection and the no-clobber guarantees."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(ROOT / "tests"))
    from _studio_fakes import dispose_widget, type_text

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


def pump(app, rounds: int = 5) -> None:
    for _ in range(rounds):
        app.processEvents()


def make_blocks():
    from manga_extract.studio.project import BlockData

    return [
        BlockData(bbox=[0, 0, 10, 10], vertical=True, source="ふはは", translation="哈哈"),
        BlockData(bbox=[0, 0, 10, 10], vertical=False, source="こんにちは"),
        BlockData(bbox=[0, 0, 10, 10], source=""),          # no text: not counted for review
    ]


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class BlocksPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.blocks_panel import BlocksPanel

        self.panel = BlocksPanel()
        self.panel.resize(340, 600)
        self.panel.show()
        self.addCleanup(dispose_widget, self.app, self.panel)
        self.blocks = make_blocks()
        self.panel.set_blocks(self.blocks)
        self.app.processEvents()
        self.log = []
        self.panel.sourceEdited.connect(lambda i, t: self.log.append(("source", i, t)))
        self.panel.translationEdited.connect(lambda i, t: self.log.append(("target", i, t)))
        self.panel.reviewedToggled.connect(lambda i, c: self.log.append(("review", i, c)))
        self.panel.retranslateRequested.connect(lambda i: self.log.append(("retranslate", i)))
        self.panel.cardSelected.connect(lambda i: self.log.append(("selected", i)))

    # -- content ------------------------------------------------------------
    def test_one_card_per_block_in_order_with_their_text(self):
        self.assertEqual(self.panel.card_count(), 3)
        card = self.panel.card(0)
        self.assertEqual((card.source_edit.toPlainText(), card.target_edit.toPlainText()),
                         ("ふはは", "哈哈"))
        self.assertEqual(self.panel.card(1).target_edit.toPlainText(), "")
        self.assertEqual(card.badge.text(), "#1")
        self.assertEqual(card.direction.text(), "竖排")
        self.assertEqual(self.panel.card(1).direction.text(), "横排")

    def test_progress_counts_only_blocks_that_have_text(self):
        self.assertEqual(self.panel.progress.text(), "已校对 0/2")
        self.blocks[0].reviewed = True
        self.panel.refresh(self.blocks)
        self.assertEqual(self.panel.progress.text(), "已校对 1/2")

    # -- bulk review --------------------------------------------------------
    def test_review_all_button_asks_for_the_bulk_action(self):
        self.panel.reviewAllRequested.connect(lambda: self.log.append(("review_all",)))
        self.panel.review_all_button.click()
        self.assertEqual(self.log[-1], ("review_all",))

    def test_review_all_button_is_enabled_only_while_some_block_has_text(self):
        self.assertTrue(self.panel.review_all_button.isEnabled())
        from manga_extract.studio.project import BlockData

        self.panel.set_blocks([BlockData(bbox=[0, 0, 10, 10], source="   ")])
        self.assertFalse(self.panel.review_all_button.isEnabled())
        self.panel.set_blocks(self.blocks)
        self.assertTrue(self.panel.review_all_button.isEnabled())
        self.panel.show_message("这一页没有检测到文字。")
        self.assertFalse(self.panel.review_all_button.isEnabled())

    def test_review_all_button_does_not_tick_anything_by_itself(self):
        # The panel only asks; the model belongs to the window (progress stays untouched).
        self.panel.review_all_button.click()
        self.assertEqual(self.panel.progress.text(), "已校对 0/2")
        self.assertFalse(any(block.reviewed for block in self.blocks))

    def test_no_blocks_shows_a_hint_instead_of_cards(self):
        self.panel.set_blocks([])
        self.assertEqual(self.panel.card_count(), 0)
        self.assertIn("没有检测到文字", self.panel.hint.text())
        self.assertTrue(self.panel.hint.isVisible())

    def test_show_message_clears_the_cards(self):
        self.panel.show_message("还没有识别")
        self.assertEqual(self.panel.card_count(), 0)
        self.assertEqual(self.panel.hint.text(), "还没有识别")
        self.assertEqual(self.panel.progress.text(), "")

    # -- editing signals ----------------------------------------------------
    def test_typing_reports_a_user_edit_with_the_card_index(self):
        edit = self.panel.card(1).target_edit
        edit.setFocus()
        type_text(edit, "你好")
        self.assertEqual(self.log[-1], ("target", 1, "你好"))
        QTest.keyClicks(self.panel.card(0).source_edit, "!")
        self.assertEqual(self.log[-1][:2], ("source", 0))

    def test_programmatic_updates_never_look_like_user_edits(self):
        self.blocks[1].translation = "机翻结果"
        self.panel.refresh(self.blocks)
        self.assertEqual(self.panel.card(1).target_edit.toPlainText(), "机翻结果")
        self.assertEqual([e for e in self.log if e[0] in ("source", "target")], [])

    def test_review_tick_reports_and_updates_progress_but_sync_does_not_report(self):
        self.panel.card(0).review_box.setChecked(True)
        self.assertEqual(self.log[-1], ("review", 0, True))
        self.assertEqual(self.panel.progress.text(), "已校对 1/2")
        self.log.clear()
        self.blocks[1].reviewed = True
        self.panel.refresh(self.blocks)                          # programmatic: silent
        self.assertEqual(self.log, [])
        self.assertTrue(self.panel.card(1).review_box.isChecked())

    def test_retranslate_button_reports_and_is_off_for_reviewed_or_empty_blocks(self):
        self.panel.card(0).retranslate_button.click()
        self.assertEqual(self.log[-1], ("retranslate", 0))
        self.assertFalse(self.panel.card(2).retranslate_button.isEnabled())   # no text
        self.blocks[0].reviewed = True
        self.panel.refresh(self.blocks)
        self.assertFalse(self.panel.card(0).retranslate_button.isEnabled())
        self.assertIn("已校对", self.panel.card(0).retranslate_button.toolTip())

    # -- refresh must not disturb typing -------------------------------------
    def test_refresh_keeps_a_trailing_space_the_user_is_typing(self):
        edit = self.panel.card(0).source_edit
        edit.setFocus()
        edit.moveCursor(edit.textCursor().MoveOperation.End)
        QTest.keyClicks(edit, " ")                               # "ふはは " (model stores stripped)
        self.assertEqual(edit.toPlainText(), "ふはは ")
        cursor_before = edit.textCursor().position()
        self.panel.refresh(self.blocks)
        self.assertEqual(edit.toPlainText(), "ふはは ")          # untouched
        self.assertEqual(edit.textCursor().position(), cursor_before)

    def test_refresh_with_a_different_block_count_rebuilds(self):
        self.panel.refresh(self.blocks[:1])
        self.assertEqual(self.panel.card_count(), 1)

    # -- selection ----------------------------------------------------------
    def test_select_highlights_without_emitting(self):
        self.panel.select(1)
        self.assertEqual(self.panel.selected_index, 1)
        self.assertEqual([e for e in self.log if e[0] == "selected"], [])
        self.assertTrue(self.panel.card(1)._selected)
        self.assertFalse(self.panel.card(0)._selected)
        self.panel.select(9)
        self.assertEqual(self.panel.selected_index, -1)

    def test_focusing_a_card_emits_once_until_the_selection_changes(self):
        self.panel.card(1).target_edit.setFocus()
        self.panel.card(1).source_edit.setFocus()                # same card again
        self.assertEqual([e for e in self.log if e[0] == "selected"], [("selected", 1)])
        self.panel.card(0).source_edit.setFocus()
        self.assertEqual([e for e in self.log if e[0] == "selected"],
                         [("selected", 1), ("selected", 0)])

    def test_clicking_the_card_frame_selects_it(self):
        QTest.mouseClick(self.panel.card(2), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, self.panel.card(2).rect().center())
        self.assertEqual(self.panel.selected_index, 2)
        self.assertIn(("selected", 2), self.log)

    def test_selecting_scrolls_the_card_into_view(self):
        many = [make_blocks()[0] for _ in range(30)]
        self.panel.set_blocks(many)
        pump(self.app)                                # let the layout settle
        bar = self.panel.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), 0)
        self.panel.select(25, scroll=True)
        pump(self.app)
        self.assertGreater(bar.value(), 0)

    def test_theme_restyle_marks_the_selected_card_with_the_accent(self):
        self.panel.apply_theme(False, QColor("#ff0000"))
        self.panel.select(0)
        self.assertIn("#ff0000", self.panel.card(0).styleSheet())
        self.assertNotIn("#ff0000", self.panel.card(1).styleSheet())

    def test_tab_moves_focus_instead_of_typing_a_tab(self):
        self.assertTrue(self.panel.card(0).source_edit.tabChangesFocus())


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AutoHeightEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.blocks_panel import AutoHeightEdit

        self.edit = AutoHeightEdit("原文")
        self.edit.resize(300, 40)
        self.edit.show()
        self.addCleanup(dispose_widget, self.app, self.edit)
        self.app.processEvents()

    def test_grows_with_lines_and_shrinks_back(self):
        one_line = self.edit.height()
        self.edit.set_text("\n".join(["行"] * 4))
        self.app.processEvents()
        self.assertGreater(self.edit.height(), one_line)
        self.edit.set_text("")
        self.app.processEvents()
        self.assertEqual(self.edit.height(), one_line)

    def test_height_is_capped_and_scrolls_beyond_the_cap(self):
        from manga_extract.studio.blocks_panel import MAX_EDIT_LINES

        one_line = self.edit.height()
        self.edit.set_text("\n".join(["行"] * 40))
        pump(self.app)
        line = self.edit.fontMetrics().lineSpacing()
        self.assertGreater(self.edit.height(), one_line)                    # it did grow ...
        self.assertLessEqual(self.edit.height(), line * (MAX_EDIT_LINES + 1) + 30)  # ... but is capped
        self.assertGreater(self.edit.verticalScrollBar().maximum(), 0)     # and scrollable

    def test_set_text_is_silent_and_user_typing_is_not(self):
        seen = []
        self.edit.userChanged.connect(seen.append)
        self.edit.set_text("程序设置")
        self.assertEqual(seen, [])
        self.edit.setFocus()
        QTest.keyClicks(self.edit, "x")
        self.assertEqual(len(seen), 1)

    def test_pasting_rich_text_keeps_plain_text_only(self):
        self.assertFalse(self.edit.acceptRichText())


if __name__ == "__main__":
    unittest.main()
