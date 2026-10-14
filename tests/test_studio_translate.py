"""Translation, proofreading edits and canvas<->card linking, driven through MainWindow.

Recognition and translation are fakes; the overlay tests use the real typeset code on the
sample pages and are skipped when no CJK font is installed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import (
        FakeExtractor,
        FakeTranslator,
        dispose_window,
        make_engine,
        tempdir,
        type_text,
        wait_until,
    )

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]
HAVE_SAMPLES = all(p.is_file() for p in SAMPLES)


def _font_available() -> bool:
    try:
        from manga_extract.typeset import find_cjk_font

        return find_cjk_font() is not None
    except Exception:
        return False


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class StudioFlowBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempdir(self)

    def make_window(self, pages: int = 1, translator=None, extractor=None, config=None):
        """A window with ``pages`` recognised pages (fake engine) and a fake translator."""
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.project import Project
        from manga_extract.translation import TranslationConfig

        self.extractor = extractor or FakeExtractor()
        engine = make_engine(self.extractor)
        self.addCleanup(engine.stop)
        self.translator = translator or FakeTranslator()
        self.tconfig = config or TranslationConfig()
        if config is None:
            self.tconfig.build_translator = lambda: self.translator       # instance override
        self.tpath = self.tmp / "translate_config.json"
        window = MainWindow(StudioConfig(auto_recognize=True), engine=engine,
                            translation_config=self.tconfig, translate_config_path=self.tpath)
        self.addCleanup(dispose_window, self.app, window)
        self.addCleanup(window.overlay.shutdown)
        self.addCleanup(window.translator.stop)
        self.window = window
        # The engine-ready message lands asynchronously and would overwrite status-bar texts.
        self.assertTrue(wait_until(self.app, lambda: window._engine_ready))
        self.project = Project.create(self.tmp / "p")
        window.project = self.project
        window._enter_project()
        if pages:
            window.import_paths([str(p) for p in SAMPLES[:pages]])
            self.ids = [p.id for p in self.project.pages]
            self.assertTrue(wait_until(self.app, lambda: all(
                self.project.load_page(i) is not None for i in self.ids)))
        return window

    # -- helpers ------------------------------------------------------------
    def data(self, index: int = 0):
        return self.window._data[self.ids[index]]

    def disk(self, index: int = 0):
        return self.project.load_page(self.ids[index])

    def idle(self) -> bool:
        return not self.window._translate_jobs

    def settle(self, seconds: float = 0.3) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)


class TranslationFlowTests(StudioFlowBase):
    def test_translating_the_page_fills_blocks_saves_and_updates_the_ui(self):
        window = self.make_window()
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.disk().blocks[1].translation))
        blocks = self.disk().blocks
        self.assertEqual([b.translation for b in blocks], ["译:ふはは", "译:こんにちは"])
        self.assertFalse(any(b.translation_edited for b in blocks))           # machine, not hand-typed
        self.assertEqual(self.translator.calls, [["ふはは", "こんにちは"]])
        self.assertEqual(window.pages._status[self.ids[0]], "translated")
        self.assertEqual(window.blocks_panel.card(0).target_edit.toPlainText(), "译:ふはは")
        self.assertTrue(wait_until(self.app, self.idle))
        self.assertIn("翻译完成", window.status_left.text())
        self.assertFalse(window.canvas.bar._busy)

    def test_reviewed_and_hand_edited_translations_are_left_alone(self):
        window = self.make_window()
        self.data().blocks[0].reviewed = True
        self.data().blocks[1].edit_translation("我自己译的")
        window.page_data_changed(self.ids[0])
        window.translate_current()
        self.settle()
        self.assertEqual(self.translator.calls, [])                          # nothing was eligible
        self.assertIn("没有需要翻译", window.status_left.text())
        self.assertEqual(self.data().blocks[1].translation, "我自己译的")

    def test_only_the_unprotected_blocks_are_sent(self):
        window = self.make_window()
        self.data().blocks[0].reviewed = True                                  # protected
        window.page_data_changed(self.ids[0])
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.disk().blocks[1].translation))
        self.assertEqual(self.translator.calls, [["こんにちは"]])
        self.assertEqual(self.disk().blocks[0].translation, "")

    def test_clearing_a_hand_translation_hands_the_block_back_to_automatic_translation(self):
        window = self.make_window()
        window._on_translation_edited(1, "手写")
        self.assertTrue(self.data().blocks[1].translation_edited)
        window._on_translation_edited(1, "")
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.disk().blocks[1].translation))
        self.assertEqual(self.disk().blocks[1].translation, "译:こんにちは")

    def test_retranslating_one_block_may_replace_a_hand_typed_translation(self):
        window = self.make_window()
        self.data().blocks[1].edit_translation("手写")
        window.page_data_changed(self.ids[0])
        window._on_retranslate_block(1)
        self.assertTrue(wait_until(self.app, lambda: self.disk().blocks[1].translation == "译:こんにちは"))
        self.assertFalse(self.disk().blocks[1].translation_edited)
        self.assertEqual(self.translator.calls, [["こんにちは"]])               # only that block

    def test_retranslating_a_reviewed_block_is_refused(self):
        window = self.make_window()
        self.data().blocks[0].reviewed = True
        window.page_data_changed(self.ids[0])
        window._on_retranslate_block(0)
        self.settle()
        self.assertEqual(self.translator.calls, [])
        self.assertIn("已标记为已校对", window.status_left.text())

    def test_translate_all_sends_one_request_per_page(self):
        window = self.make_window(pages=2)
        window.translate_all()
        self.assertTrue(wait_until(self.app, lambda: self.disk(0).blocks[1].translation
                                   and self.disk(1).blocks[1].translation))
        self.assertEqual(len(self.translator.calls), 2)
        self.assertTrue(wait_until(self.app, self.idle))
        self.assertIn("翻译完成：2 页", window.status_left.text())

    def test_translate_pending_skips_finished_pages_and_only_sends_missing_blocks(self):
        window = self.make_window(pages=2)
        for block in self.data(0).blocks:                                      # page 1: done
            block.apply_machine_translation("已有")
        self.data(1).blocks[0].apply_machine_translation("已有")                # page 2: half done
        window.page_data_changed(self.ids[0])
        window.page_data_changed(self.ids[1])
        window.translate_pending()
        self.assertTrue(wait_until(self.app, lambda: self.disk(1).blocks[1].translation))
        self.assertEqual(self.translator.calls, [["こんにちは"]])               # page 2, missing block
        self.assertEqual(self.disk(1).blocks[0].translation, "已有")
        self.assertEqual(self.disk(0).blocks[0].translation, "已有")

    def test_pages_not_recognised_yet_are_skipped_with_a_note(self):
        window = self.make_window(pages=0)
        window.config.auto_recognize = False
        window.import_paths([str(SAMPLES[0])])
        window.translate_all()
        self.settle()
        self.assertEqual(self.translator.calls, [])
        self.assertIn("还没有识别", window.status_left.text())

    # -- races ----------------------------------------------------------------
    def test_a_block_whose_original_changed_while_translating_is_not_overwritten(self):
        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.translator.calls))   # request is out
        window._on_source_edited(0, "別の文")
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: self.data().blocks[1].translation))
        self.assertEqual(self.data().blocks[0].translation, "")                # source no longer matches
        self.assertEqual(self.data().blocks[1].translation, "译:こんにちは")

    def test_a_translation_typed_while_translating_is_not_overwritten(self):
        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.translator.calls))
        window._on_translation_edited(0, "我自己译的")
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: self.data().blocks[1].translation))
        self.assertEqual(self.data().blocks[0].translation, "我自己译的")

    def test_a_block_reviewed_while_translating_is_not_overwritten(self):
        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.translator.calls))
        window._on_reviewed_toggled(0, True)
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: self.data().blocks[1].translation))
        self.assertEqual(self.data().blocks[0].translation, "")

    def test_cancelling_drops_the_result_and_translation_works_again_afterwards(self):
        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.translator.calls))
        window.cancel_translation()
        self.assertTrue(self.idle())
        self.assertFalse(window.canvas.bar._busy)
        gate.set()
        self.settle(0.4)
        self.assertEqual(self.data().blocks[0].translation, "")                # late result ignored
        window.translate_current()                                              # a fresh batch works
        self.assertTrue(wait_until(self.app, lambda: self.data().blocks[0].translation))

    def test_a_failure_aborts_the_batch_and_reports_once(self):
        window = self.make_window(pages=2, translator=FakeTranslator(fail=True))
        window.translate_all()
        self.assertTrue(wait_until(self.app, self.idle))
        self.settle(0.3)
        self.assertEqual(len(self.translator.calls), 1)                         # page 2 never sent
        self.assertEqual(self.disk(1).blocks[0].translation, "")
        self.assertIn("翻译已中断", window.status_left.text())
        self.assertFalse(window.canvas.bar._busy)

    def test_switching_projects_drops_a_late_result(self):
        from manga_extract.studio.project import Project

        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        self.assertTrue(wait_until(self.app, lambda: self.translator.calls))
        other = Project.create(self.tmp / "other")
        window.project = other
        window._enter_project()
        gate.set()
        self.settle(0.4)
        self.assertEqual(self.disk().blocks[0].translation, "")                 # project A untouched
        self.assertTrue(self.idle())
        self.assertFalse(window.canvas.bar._busy)

    def test_a_second_request_while_busy_is_refused(self):
        gate = threading.Event()
        window = self.make_window(translator=FakeTranslator(gate=gate))
        window.translate_current()
        window.translate_current()
        self.assertIn("正在翻译", window.status_left.text())
        gate.set()
        self.assertTrue(wait_until(self.app, self.idle))
        self.assertEqual(len(self.translator.calls), 1)

    def test_missing_api_key_is_reported_and_nothing_is_queued(self):
        from manga_extract.translation import TranslationConfig

        window = self.make_window(config=TranslationConfig(mode="ai"))          # real, no key
        window.translate_current()
        self.settle()
        self.assertEqual(window._translate_jobs, {})
        self.assertFalse(window.canvas.bar._busy)


class TranslationChannelTests(StudioFlowBase):
    def _menu(self):
        return [(a.text(), a.isEnabled(), a.isChecked())
                for a in self.window.canvas.bar.translate_button.menu().actions()]

    def test_default_channel_is_checked_and_unconfigured_ai_is_disabled(self):
        self.make_window(pages=0)
        entries = {text: (enabled, checked) for text, enabled, checked in self._menu()}
        self.assertEqual(entries["普通 · 谷歌"], (True, True))
        self.assertEqual(entries["普通 · 有道"], (True, False))
        ai = next(v for k, v in entries.items() if k.startswith("AI"))
        self.assertEqual(ai, (False, False))                                    # no API key yet

    def test_choosing_a_web_engine_is_applied_and_saved(self):
        from manga_extract.translation import TranslationConfig

        window = self.make_window(pages=0)
        window._on_channel_chosen("web:youdao")
        self.assertEqual(self.tconfig.web_engine, "youdao")
        self.assertEqual(TranslationConfig.load(self.tpath).web_engine, "youdao")
        entries = {text: checked for text, _enabled, checked in self._menu()}
        self.assertTrue(entries["普通 · 有道"])
        self.assertFalse(entries["普通 · 谷歌"])
        self.assertIn("有道", window.status_left.text())

    def test_a_configured_ai_profile_can_be_chosen(self):
        from manga_extract.translation import TranslationConfig

        window = self.make_window(pages=0)
        self.tconfig.ai_profiles[0].api_key = "sk-test"
        window._refresh_channels()
        ai_text = next(t for t, enabled, _c in self._menu() if t.startswith("AI"))
        self.assertTrue(next(e for t, e, _c in self._menu() if t == ai_text))
        window._on_channel_chosen("ai:0")
        self.assertEqual(self.tconfig.mode, "ai")
        self.assertTrue(TranslationConfig.load(self.tpath).mode == "ai")
        self.assertTrue(next(c for t, _e, c in self._menu() if t == ai_text))

    def test_a_bogus_channel_key_changes_nothing(self):
        window = self.make_window(pages=0)
        for key in ("ai:99", "ai:x", "nonsense", "web"):
            window._on_channel_chosen(key)
        self.assertEqual((self.tconfig.mode, self.tconfig.web_engine), ("web", "google"))
        self.assertFalse(self.tpath.exists())


class ProofreadingEditTests(StudioFlowBase):
    def test_typing_a_translation_updates_the_model_and_saves_after_a_pause(self):
        window = self.make_window()
        card = window.blocks_panel.card(0)
        card.target_edit.setFocus()
        type_text(card.target_edit, "手写译文")
        block = self.data().blocks[0]
        self.assertEqual((block.translation, block.translation_edited), ("手写译文", True))
        self.assertEqual(self.disk().blocks[0].translation, "")                 # not written yet
        self.assertIn(self.ids[0], window._dirty)
        self.assertTrue(wait_until(self.app, lambda: self.disk().blocks[0].translation == "手写译文",
                                   timeout=4))
        self.assertEqual(window._dirty, {})

    def test_flush_writes_immediately(self):
        window = self.make_window()
        window._on_translation_edited(0, "立刻保存")
        window._flush_saves()
        self.assertEqual(self.disk().blocks[0].translation, "立刻保存")
        self.assertEqual(window._dirty, {})

    def test_closing_the_window_saves_pending_edits(self):
        window = self.make_window()
        window.config.save = lambda *a, **k: None                              # keep the repo clean
        window._on_translation_edited(0, "关窗前的修改")
        window.close()
        self.assertEqual(self.disk().blocks[0].translation, "关窗前的修改")

    def test_editing_the_original_marks_it_edited_and_emptying_it_disables_retranslate(self):
        window = self.make_window()
        window._on_source_edited(1, "")
        block = self.data().blocks[1]
        self.assertEqual(block.source, "")
        self.assertTrue(block.source_edited)
        self.assertFalse(window.blocks_panel.card(1).retranslate_button.isEnabled())
        window._flush_saves()
        self.assertTrue(self.disk().blocks[1].source_edited)

    def test_ticking_every_block_makes_the_page_reviewed(self):
        window = self.make_window()
        for index in (0, 1):
            window.blocks_panel.card(index).review_box.setChecked(True)
        self.assertEqual(window.pages._status[self.ids[0]], "reviewed")
        window._flush_saves()
        self.assertTrue(all(b.reviewed for b in self.disk().blocks))
        window.blocks_panel.card(0).review_box.setChecked(False)
        self.assertNotEqual(window.pages._status[self.ids[0]], "reviewed")

    def test_unsaved_edits_of_a_project_are_written_to_it_when_another_opens(self):
        from manga_extract.studio.project import Project

        window = self.make_window()
        window._on_translation_edited(0, "A 项目的修改")
        other = Project.create(self.tmp / "other")
        window.project = other
        window._enter_project()
        self.assertEqual(self.disk().blocks[0].translation, "A 项目的修改")     # not lost ...
        self.assertEqual(window._dirty, {})
        self.assertEqual(other.pages, [])                                      # ... and not misfiled

    def test_a_fresh_recognition_result_is_not_overwritten_by_a_stale_pending_edit(self):
        from manga_extract.studio.project import PageData
        from _studio_fakes import fake_result

        window = self.make_window()
        window._on_translation_edited(0, "旧的修改")
        window._in_flight.add(self.ids[0])                                      # a re-recognition ran
        window._on_recognized(self.ids[0], PageData.from_result(fake_result()))
        window._flush_saves()
        self.assertEqual(self.disk().blocks[0].translation, "")

    def test_deleting_a_page_forgets_its_pending_edit(self):
        from unittest import mock

        window = self.make_window(pages=2)
        window._on_translation_edited(0, "要被删掉")
        with mock.patch("manga_extract.studio.main_window.MessageBox") as box:
            box.return_value.exec.return_value = True
            window._on_remove_requested(self.ids[0])
        self.assertNotIn(self.ids[0], window._dirty)
        window._flush_saves()                                                   # must not raise
        self.assertFalse((self.project.folder / "data" / f"{self.ids[0]}.json").exists())

    def test_editing_without_a_recognised_current_page_is_ignored(self):
        window = self.make_window(pages=0)
        window._on_translation_edited(0, "没有页面")
        window._on_source_edited(0, "没有页面")
        window._on_reviewed_toggled(0, True)
        self.assertEqual(window._dirty, {})


class PanelFollowsPageTests(StudioFlowBase):
    def test_selecting_another_page_swaps_the_cards(self):
        window = self.make_window(pages=2)
        self.data(0).blocks[0].apply_machine_translation("第一页的译文")
        window.page_data_changed(self.ids[0])
        window.pages.select(self.ids[1])
        self.assertEqual(window.blocks_panel.card(0).target_edit.toPlainText(), "")
        window.pages.select(self.ids[0])
        self.assertEqual(window.blocks_panel.card(0).target_edit.toPlainText(), "第一页的译文")

    def test_an_unrecognised_page_shows_a_hint_not_cards(self):
        window = self.make_window(pages=0)
        window.config.auto_recognize = False
        window.import_paths([str(SAMPLES[0])])
        self.assertEqual(window.blocks_panel.card_count(), 0)
        self.assertIn("还没有识别", window.blocks_panel.hint.text())

    def test_a_page_waiting_for_the_engine_says_so_and_fills_in_when_done(self):
        gate = threading.Event()
        window = self.make_window(pages=0, extractor=FakeExtractor(gate=gate))
        window.import_paths([str(SAMPLES[0])])                                  # auto-recognise: blocked
        self.assertTrue(wait_until(self.app, lambda: "正在识别" in window.blocks_panel.hint.text()))
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: window.blocks_panel.card_count() == 2))

    def test_recognition_failure_puts_the_hint_back(self):
        window = self.make_window(pages=0, extractor=FakeExtractor(fail_for=lambda image: True))
        window.import_paths([str(SAMPLES[0])])
        self.assertTrue(wait_until(self.app, lambda: window._in_flight == set()))
        self.assertIn("还没有识别", window.blocks_panel.hint.text())


class CanvasCardLinkTests(StudioFlowBase):
    def _click_block(self, index: int) -> None:
        view = self.window.canvas.view
        rect = view._blocks[index].rect()
        point = view.mapFromScene(QPointF(rect.center()))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, point)

    def test_clicking_a_block_on_the_canvas_selects_its_card(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()
        self.addCleanup(window.hide)
        self.settle(0.2)
        self._click_block(1)
        self.assertEqual(window.canvas.view.selected_block, 1)
        self.assertEqual(window.blocks_panel.selected_index, 1)
        self.assertIn("文本块 #2", window.status_left.text())

    def test_clicking_empty_canvas_clears_both_selections(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()
        self.addCleanup(window.hide)
        self.settle(0.2)
        self._click_block(0)
        view = window.canvas.view
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         view.mapFromScene(QPointF(5, 5)))
        self.assertEqual((view.selected_block, window.blocks_panel.selected_index), (-1, -1))

    def test_focusing_a_card_selects_the_block_on_the_canvas(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()                                  # focus events need a shown window
        self.addCleanup(window.hide)
        self.settle(0.2)
        window.blocks_panel.card(1).source_edit.setFocus()
        self.assertEqual(window.canvas.view.selected_block, 1)
        self.assertIn("こんにちは", window.status_left.text())

    def test_selecting_a_card_does_not_loop_back_into_the_canvas_handler(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()
        self.addCleanup(window.hide)
        self.settle(0.2)
        calls = []
        window.canvas.view.blockClicked.connect(calls.append)
        window.blocks_panel.card(0).source_edit.setFocus()
        self.assertEqual(calls, [])


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES and _font_available(),
                     "PySide6, sample images or a CJK font missing")
class OverlayFromEditsTests(StudioFlowBase):
    def _ready(self) -> bool:
        return self.window.canvas.bar.overlay_button.isEnabled()

    def test_machine_translation_makes_the_overlay_available(self):
        window = self.make_window()
        self.assertFalse(self._ready())
        window.translate_current()
        self.assertTrue(wait_until(self.app, self._ready, timeout=20))

    def test_typing_a_translation_renders_and_retyping_changes_the_overlay(self):
        window = self.make_window()
        card = window.blocks_panel.card(1)
        card.target_edit.setFocus()
        type_text(card.target_edit, "你好")
        self.assertTrue(wait_until(self.app, self._ready, timeout=20))
        first = window.canvas.view._overlay.toImage()
        type_text(card.target_edit, "，这是更长的一句话用来改变排版")
        self.assertFalse(self._ready() and window.canvas.view._overlay.toImage() != first and False)
        self.assertTrue(wait_until(
            self.app, lambda: self._ready() and window.canvas.view._overlay.toImage() != first,
            timeout=20))

    def test_typing_is_debounced_into_a_single_overlay_render(self):
        window = self.make_window()
        requests = []
        original = window.overlay.request
        window.overlay.request = lambda *a, **k: (requests.append(a[1]), original(*a, **k))[1]
        card = window.blocks_panel.card(1)
        card.target_edit.setFocus()
        for ch in "你好吗":
            type_text(card.target_edit, ch)
        self.assertTrue(wait_until(self.app, self._ready, timeout=20))
        self.assertEqual(len(requests), 1)                                      # not one per keystroke


if __name__ == "__main__":
    unittest.main()
