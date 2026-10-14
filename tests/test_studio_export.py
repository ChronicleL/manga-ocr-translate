"""ExportDialog and the export menu of the main window (offscreen)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
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
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import dispose_widget, tempdir, wait_until
    from test_studio_import import ImportHarness

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
class DialogBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.project import BlockData, PageData, Project

        self.tmp = tempdir(self)
        self.project = Project.create(self.tmp / "p")
        self.project.add_image_files(SAMPLES)
        self.ids = [p.id for p in self.project.pages]
        self.project.save_page(self.ids[0], PageData(713, 861, [
            BlockData(bbox=[500, 50, 560, 300], vertical=True, source="ふはは", translation="哈哈哈")]))
        self.project.save_page(self.ids[1], PageData(716, 891, [
            BlockData(bbox=[100, 600, 300, 680], source="こんにちは")]))
        self.out = self.tmp / "out"

    def dialog(self, exporter=None, folder=""):
        from manga_extract.studio.export import export_chapter
        from manga_extract.studio.export_dialog import ExportDialog

        dlg = ExportDialog(None, self.project, folder, exporter=exporter or export_chapter)
        dlg.show()
        self.addCleanup(dispose_widget, self.app, dlg)
        self.app.processEvents()
        return dlg


class ExportDialogTests(DialogBase):
    def test_defaults_and_the_page_summary(self):
        dlg = self.dialog(folder=str(self.out))
        self.assertEqual(dlg.folder_edit.text(), str(self.out))
        self.assertEqual(dlg.format_box.currentIndex(), 0)
        self.assertFalse(dlg.quality_box.isEnabled())                         # only for JPG
        self.assertTrue(dlg.originals_check.isChecked())
        self.assertIn("共 2 页，其中 1 页有译文", dlg.summary.text())
        self.assertTrue(dlg.start_button.isEnabled())
        self.assertFalse(dlg.open_button.isVisible())

    def test_the_quality_box_follows_the_format(self):
        dlg = self.dialog()
        dlg.format_box.setCurrentIndex(1)
        self.assertTrue(dlg.quality_box.isEnabled())
        dlg.format_box.setCurrentIndex(0)
        self.assertFalse(dlg.quality_box.isEnabled())

    def test_the_options_reflect_the_form(self):
        dlg = self.dialog(folder=str(self.out))
        dlg.format_box.setCurrentIndex(1)
        dlg.quality_box.setCurrentIndex(2)
        dlg.originals_check.setChecked(False)
        options = dlg.options()
        self.assertEqual((options.folder, options.fmt, options.quality, options.include_untranslated),
                         (self.out, "jpg", 80, False))

    def test_starting_without_a_folder_asks_for_one(self):
        calls = []
        dlg = self.dialog(exporter=lambda *a, **k: calls.append(1))
        dlg.start_button.click()
        self.assertIn("文件夹", dlg.status.text())
        self.assertEqual(calls, [])

    def test_an_empty_project_cannot_start(self):
        from manga_extract.studio.project import Project

        self.project = Project.create(self.tmp / "empty")
        self.assertFalse(self.dialog().start_button.isEnabled())

    def test_browse_fills_the_folder_and_cancelling_keeps_it(self):
        dlg = self.dialog(folder="keep-me")
        with mock.patch("manga_extract.studio.export_dialog.QFileDialog.getExistingDirectory",
                        return_value=str(self.out)):
            dlg.browse()
        self.assertEqual(dlg.folder(), str(self.out))
        with mock.patch("manga_extract.studio.export_dialog.QFileDialog.getExistingDirectory",
                        return_value=""):
            dlg.browse()
        self.assertEqual(dlg.folder(), str(self.out))

    def test_a_real_export_writes_the_files_and_reports(self):
        dlg = self.dialog(folder=str(self.out))
        dlg.start_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None, timeout=20))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["001.png", "002.png"])
        self.assertEqual(dlg.progress.value(), 2)
        self.assertIn("已导出 2 页", dlg.status.text())
        self.assertTrue(dlg.open_button.isVisible())
        self.assertEqual(dlg.start_button.text(), "再次导出")
        self.assertTrue(dlg.start_button.isEnabled() and dlg.folder_edit.isEnabled())
        self.assertEqual(dlg.close_button.text(), "关闭")

    def test_jpg_and_leaving_out_untranslated_pages(self):
        dlg = self.dialog(folder=str(self.out))
        dlg.format_box.setCurrentIndex(1)
        dlg.originals_check.setChecked(False)
        dlg.start_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None, timeout=20))
        self.assertEqual([p.name for p in self.out.iterdir()], ["001.jpg"])   # page 2 has no translation
        self.assertIn("跳过 1 页", dlg.status.text())

    def test_the_controls_are_locked_while_exporting(self):
        gate = threading.Event()
        dlg = self.dialog(folder=str(self.out),
                          exporter=lambda project, options, progress, cancel: (gate.wait(5),
                                                                              __import__("manga_extract.studio.export", fromlist=["x"]).ExportReport())[1])
        dlg.start_button.click()
        self.assertFalse(dlg.start_button.isEnabled())
        self.assertFalse(dlg.folder_edit.isEnabled())
        self.assertFalse(dlg.format_box.isEnabled())
        self.assertEqual(dlg.close_button.text(), "取消")
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None))
        self.assertTrue(dlg.folder_edit.isEnabled())

    def test_pressing_start_twice_runs_one_export(self):
        from manga_extract.studio.export import ExportReport

        gate, calls = threading.Event(), []

        def exporter(project, options, progress, cancel):
            calls.append(1)
            gate.wait(5)
            return ExportReport()

        dlg = self.dialog(folder=str(self.out), exporter=exporter)
        dlg.start()
        dlg.start()
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None))
        self.assertEqual(len(calls), 1)

    # -- cancel / failures ---------------------------------------------------------
    def test_cancel_stops_at_the_next_page_and_the_dialog_can_then_be_closed(self):
        """Regression: the close button used to stay disabled after a cancelled export."""
        from manga_extract.studio.export import ExportReport

        started, gate = threading.Event(), threading.Event()

        def exporter(project, options, progress, cancel):
            started.set()
            gate.wait(5)
            return ExportReport(cancelled=cancel.is_set())

        dlg = self.dialog(folder=str(self.out), exporter=exporter)
        dlg.start_button.click()
        self.assertTrue(started.wait(3))
        dlg.close_button.click()                                           # "取消"
        self.assertTrue(dlg.isVisible())                                   # not closed, only asked to stop
        self.assertIn("正在取消", dlg.status.text())
        self.assertFalse(dlg.close_button.isEnabled())
        gate.set()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None))
        self.assertTrue(dlg.report.cancelled)
        self.assertIn("已取消", dlg.status.text())
        self.assertTrue(dlg.close_button.isEnabled())                      # the fix
        dlg.close_button.click()
        self.assertFalse(dlg.isVisible())

    def test_an_exception_in_the_exporter_is_reported_and_the_dialog_recovers(self):
        def boom(project, options, progress, cancel):
            raise RuntimeError("disk exploded")

        dlg = self.dialog(folder=str(self.out), exporter=boom)
        dlg.start_button.click()
        self.assertTrue(wait_until(self.app, lambda: dlg.report is not None))
        self.assertIn("disk exploded", dlg.status.text())
        self.assertTrue(dlg.start_button.isEnabled())
        self.assertFalse(dlg.open_button.isVisible())                       # nothing was written

    def test_failures_are_listed_but_capped(self):
        from manga_extract.studio.export import ExportReport
        from manga_extract.studio.export_dialog import ExportDialog

        report = ExportReport(written=[Path("001.png")], failed=[(i, f"问题{i}") for i in range(1, 8)])
        text = ExportDialog.describe(report)
        self.assertIn("7 页失败", text)
        self.assertIn("第 1 页：问题1", text)
        self.assertIn("第 4 页：问题4", text)
        self.assertNotIn("第 5 页", text)
        self.assertIn("另有 3 页失败", text)

    def test_a_missing_font_is_explained_per_page(self):
        dlg = self.dialog(folder=str(self.out))
        with mock.patch("manga_extract.typeset.find_cjk_font", return_value=None):
            dlg.start_button.click()
            self.assertTrue(wait_until(self.app, lambda: dlg.report is not None, timeout=20))
        self.assertIn("字体", dlg.status.text())
        self.assertIn("第 1 页", dlg.status.text())
        self.assertEqual(len(dlg.report.written), 1)                         # the untranslated page still went out

    def test_closing_the_dialog_cancels_a_running_export(self):
        from manga_extract.studio.export import ExportReport

        seen, gate = [], threading.Event()

        def exporter(project, options, progress, cancel):
            gate.wait(5)
            seen.append(cancel.is_set())
            return ExportReport()

        dlg = self.dialog(folder=str(self.out), exporter=exporter)
        dlg.start_button.click()
        dlg.done(0)                                                         # e.g. the window is closed
        gate.set()
        time.sleep(0.2)
        self.assertEqual(seen, [True])

    def test_a_result_arriving_after_the_dialog_is_gone_is_harmless(self):
        gate = threading.Event()

        def exporter(project, options, progress, cancel):
            gate.wait(5)
            from manga_extract.studio.export import ExportReport

            return ExportReport()

        dlg = self.dialog(folder=str(self.out), exporter=exporter)
        dlg.start_button.click()
        dispose_widget(self.app, dlg)
        gate.set()
        time.sleep(0.3)
        self.app.processEvents()                                             # must not raise


@unittest.skipUnless(HAVE_QT and HAVE_SAMPLES, "PySide6 or sample images missing")
class ExportMenuTests(ImportHarness):
    class FakeExport:
        def __init__(self, folder=""):
            self.ran, self.folder_value = False, folder

        def exec(self):
            self.ran = True
            return True

        def folder(self):
            return self.folder_value

    def ready_window(self, **kwargs):
        """A window with two recognised pages, the first one translated."""
        window = self.make_window(auto=True, **kwargs)
        window.import_paths([str(p) for p in SAMPLES])
        self.assertTrue(wait_until(self.app, lambda: all(
            self.project.load_page(p.id) is not None for p in self.project.pages)))
        self.ids = [p.id for p in self.project.pages]
        for block in window._data[self.ids[0]].blocks:
            block.apply_machine_translation("译文")
        window.page_data_changed(self.ids[0])
        return window

    def test_the_export_button_follows_the_project_state(self):
        window = self.make_window(project=False)
        self.assertFalse(window.export_button.isEnabled())
        window = self.make_window(project=True)
        self.assertTrue(window.export_button.isEnabled())

    def test_the_menu_has_the_four_exports(self):
        window = self.make_window()
        texts = [a.text() for a in window.export_button.menu().actions()]
        self.assertEqual(texts, ["整话图片…（Ctrl+E）", "当前页图片…", "文本（原文与译文）…", "JSON（含坐标与校对状态）…"])

    # -- chapter ----------------------------------------------------------------------
    def test_chapter_export_needs_a_project_and_pages(self):
        created = []
        window = self.make_window(project=False,
                                  export_dialog_factory=lambda p, pr, f: created.append(1) or self.FakeExport())
        window.export_chapter_images()
        self.assertEqual(created, [])
        window = self.make_window(project=True,
                                  export_dialog_factory=lambda p, pr, f: created.append(1) or self.FakeExport())
        window.export_chapter_images()                                       # a project without pages
        self.assertEqual(created, [])

    def test_unsaved_edits_are_written_before_the_chapter_dialog_opens(self):
        seen = {}

        def factory(parent, project, folder):
            seen["on_disk"] = project.load_page(self.ids[0]).blocks[0].translation
            return self.FakeExport()

        window = self.ready_window(export_dialog_factory=factory)
        window._on_translation_edited(0, "刚改的译文")                        # still only in memory
        self.assertEqual(self.project.load_page(self.ids[0]).blocks[0].translation, "译文")
        window.export_chapter_images()
        self.assertEqual(seen["on_disk"], "刚改的译文")

    def test_the_last_export_folder_is_remembered_and_offered_again(self):
        offered = []
        dialog = self.FakeExport(folder=str(self.tmp / "exports"))

        def factory(parent, project, folder):
            offered.append(folder)
            return dialog

        window = self.ready_window(export_dialog_factory=factory)
        window.export_chapter_images()
        window.export_chapter_images()
        self.assertEqual(offered, ["", str(self.tmp / "exports")])
        self.assertEqual(self.config.last_export_folder, str(self.tmp / "exports"))

    # -- current page -------------------------------------------------------------------
    def patch_save_dialog(self, path):
        return mock.patch("manga_extract.studio.main_window.QFileDialog.getSaveFileName",
                          return_value=(str(path), ""))

    def test_the_current_page_is_exported_with_its_translation(self):
        window = self.ready_window()
        window.pages.select(self.ids[1])                                      # an untranslated page
        target = self.tmp / "page2.png"
        with self.patch_save_dialog(target):
            window.export_current_page()
        self.assertTrue(target.is_file())
        self.assertEqual(self.config.last_export_folder, str(self.tmp))

    @unittest.skipUnless(_font_available(), "no CJK font")
    def test_a_translated_page_differs_from_the_original(self):
        window = self.ready_window()
        window.pages.select(self.ids[0])
        target = self.tmp / "page1.png"
        with self.patch_save_dialog(target):
            window.export_current_page()
        from PIL import Image

        with Image.open(target) as exported, Image.open(self.project.image_path(self.ids[0])) as original:
            self.assertEqual(exported.size, original.size)
            self.assertNotEqual(exported.convert("RGB").tobytes(), original.convert("RGB").tobytes())

    def test_the_extension_chooses_jpg(self):
        window = self.ready_window()
        target = self.tmp / "page.jpg"
        with self.patch_save_dialog(target):
            window.export_current_page()
        self.assertEqual(target.read_bytes()[:3], b"\xff\xd8\xff")

    def test_cancelling_the_save_dialog_exports_nothing(self):
        window = self.ready_window()
        with mock.patch("manga_extract.studio.main_window.QFileDialog.getSaveFileName",
                        return_value=("", "")):
            window.export_current_page()
        self.assertEqual(self.config.last_export_folder, "")

    def test_an_unwritable_target_is_reported_not_raised(self):
        window = self.ready_window()
        folder = self.tmp / "iamafolder"
        folder.mkdir()
        with self.patch_save_dialog(folder):                                  # a directory, not a file
            window.export_current_page()                                      # must not raise

    # -- text / json ---------------------------------------------------------------------
    def test_text_export_contains_originals_and_translations(self):
        window = self.ready_window()
        target = self.tmp / "chapter.txt"
        with self.patch_save_dialog(target):
            window.export_text_file()
        text = target.read_text(encoding="utf-8")
        self.assertIn("ふはは", text)
        self.assertIn("译文", text)
        self.assertIn("个文本块", window.status_left.text())

    def test_text_export_includes_edits_that_were_not_saved_yet(self):
        window = self.ready_window()
        window._on_translation_edited(0, "还没落盘的译文")
        target = self.tmp / "chapter.txt"
        with self.patch_save_dialog(target):
            window.export_text_file()
        self.assertIn("还没落盘的译文", target.read_text(encoding="utf-8"))

    def test_json_export_is_valid_and_complete(self):
        window = self.ready_window()
        target = self.tmp / "chapter.json"
        with self.patch_save_dialog(target):
            window.export_json_file()
        doc = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["pages"]), 2)
        self.assertEqual(doc["pages"][0]["blocks"][0]["translation"], "译文")

    def test_the_default_file_name_is_the_project_name(self):
        window = self.ready_window()
        captured = {}

        def fake(parent, title, start, file_filter):
            captured["start"] = start
            return "", ""

        with mock.patch("manga_extract.studio.main_window.QFileDialog.getSaveFileName", side_effect=fake):
            window.export_text_file()
        self.assertTrue(captured["start"].endswith("p.txt"))               # the project folder is called "p"

    def test_exports_need_a_project(self):
        window = self.make_window(project=False)
        with mock.patch("manga_extract.studio.main_window.QFileDialog.getSaveFileName") as dialog:
            window.export_text_file()
            window.export_json_file()
            window.export_current_page()
        dialog.assert_not_called()


class ConfigTests(unittest.TestCase):
    def test_the_export_folder_survives_in_the_saved_config(self):
        from manga_extract.studio.config import StudioConfig

        self.assertEqual(StudioConfig.from_dict({"last_export_folder": "E:\\out"}).last_export_folder, "E:\\out")
        self.assertEqual(StudioConfig.from_dict({"last_export_folder": 5}).last_export_folder, "")


if __name__ == "__main__":
    unittest.main()
