"""Unit tests for the studio project model (no Qt needed).

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manga_extract.studio.project import (  # noqa: E402
    BlockData,
    PageData,
    Project,
    ProjectError,
    STATUS_PENDING,
    STATUS_RECOGNIZED,
    STATUS_REVIEWED,
    STATUS_TRANSLATED,
    natural_key,
)

PNG = (  # a valid 1x1 PNG
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
    b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7"
    b"5\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _fake_result():
    """Shaped like ``pipeline.PageResult`` (only the attributes we read)."""
    line = SimpleNamespace(polygon=[[0, 0], [10, 0], [10, 20], [0, 20]], text="ふは")
    block = SimpleNamespace(xyxy=[0, 0, 10, 20], vertical=True, language="ja",
                            lines=[line], text="ふは")
    return SimpleNamespace(width=100, height=200, blocks=[block])


class NaturalKeyTests(unittest.TestCase):
    def test_numbers_sort_numerically(self):
        names = ["10.png", "2.png", "1.png", "a3.png", "a20.png"]
        self.assertEqual(sorted(names, key=natural_key),
                         ["1.png", "2.png", "10.png", "a3.png", "a20.png"])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _project(self) -> Project:
        return Project.create(self.root / "第12话", target_lang="zh-CN")

    def test_create_then_open_round_trip(self):
        project = self._project()
        project.add_image_bytes(PNG, ".png", "截图")
        reopened = Project.open(project.folder)
        self.assertEqual(reopened.name, "第12话")
        self.assertEqual([p.id for p in reopened.pages], ["001"])
        self.assertTrue(reopened.image_path("001").is_file())

    def test_create_refuses_existing_project(self):
        project = self._project()
        with self.assertRaises(ProjectError):
            Project.create(project.folder)

    def test_open_rejects_plain_folder(self):
        with self.assertRaises(ProjectError):
            Project.open(self.root)

    def test_open_rejects_corrupt_project_file(self):
        project = self._project()
        (project.folder / "project.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ProjectError):
            Project.open(project.folder)

    def test_folder_import_is_naturally_ordered_and_skips_non_images(self):
        src = self.root / "src"
        src.mkdir()
        for name in ("10.png", "2.png", "1.png"):
            (src / name).write_bytes(PNG)
        (src / "notes.txt").write_text("x", encoding="utf-8")
        project = self._project()
        added = project.add_image_files([src])
        self.assertEqual([e.origin for e in added], ["1.png", "2.png", "10.png"])
        self.assertEqual([e.id for e in added], ["001", "002", "003"])

    def test_blobs_are_added_in_order_with_their_origin_and_saved_once(self):
        project = self._project()
        calls = []
        original = project.save
        project.save = lambda: (calls.append(1), original())[1]
        added = project.add_image_blobs([(PNG, ".png", "https://x/1"), (PNG, ".JPG", "https://x/2"),
                                         (PNG, ".bin", "https://x/3")])
        self.assertEqual([e.id for e in added], ["001", "002", "003"])
        self.assertEqual([e.origin for e in added], ["https://x/1", "https://x/2", "https://x/3"])
        self.assertEqual([e.file[-4:] for e in added], [".png", ".jpg", ".png"])   # suffix normalised
        self.assertEqual(len(calls), 1)                                          # one write, not three
        self.assertEqual([p.origin for p in Project.open(project.folder).pages],
                         ["https://x/1", "https://x/2", "https://x/3"])

    def test_adding_no_blobs_writes_nothing(self):
        project = self._project()
        calls = []
        original = project.save
        project.save = lambda: (calls.append(1), original())[1]
        self.assertEqual(project.add_image_blobs([]), [])
        self.assertEqual(calls, [])

    def test_non_image_extension_falls_back_to_png(self):
        project = self._project()
        entry = project.add_image_bytes(PNG, ".bin", "x")
        self.assertTrue(entry.file.endswith(".png"))

    def test_next_id_goes_past_highest_existing(self):
        project = self._project()
        for _ in range(3):
            project.add_image_bytes(PNG)
        project.save_page("003", PageData(1, 1))
        project.remove_page("003")  # page and its data file are both gone
        self.assertFalse(project.data_path("003").exists())
        self.assertEqual(project.add_image_bytes(PNG).id, "003")  # freed, so reusable
        project.save_page("002", PageData(1, 1))
        project.remove_page("002")
        # The new 003 still exists, so the next id must go past it.
        self.assertEqual(project.add_image_bytes(PNG).id, "004")

    def test_move_page_reorders_and_persists(self):
        project = self._project()
        for _ in range(3):
            project.add_image_bytes(PNG)
        project.move_page("003", 0)
        self.assertEqual([p.id for p in Project.open(project.folder).pages],
                         ["003", "001", "002"])

    def test_unknown_page_raises(self):
        project = self._project()
        with self.assertRaises(ProjectError):
            project.entry("999")
        with self.assertRaises(ProjectError):
            project.save_page("999", PageData(1, 1))


class PageDataTests(unittest.TestCase):
    def test_from_result_copies_geometry_and_text(self):
        data = PageData.from_result(_fake_result())
        self.assertEqual((data.width, data.height), (100, 200))
        block = data.blocks[0]
        self.assertEqual(block.source, "ふは")
        self.assertTrue(block.vertical)
        self.assertEqual(block.xyxy, [0, 0, 10, 20])  # typeset reads .xyxy

    def test_dict_round_trip_keeps_edits(self):
        data = PageData.from_result(_fake_result())
        data.blocks[0].set_translation("哈哈")
        data.blocks[0].reviewed = True
        again = PageData.from_dict(json.loads(json.dumps(data.to_dict())))
        self.assertEqual(again.blocks[0].translation, "哈哈")
        self.assertTrue(again.blocks[0].reviewed)

    def test_reads_legacy_page_result_json(self):
        legacy = {  # shape of PageResult.to_dict(): "text", no "source"/"translation"
            "source": "1.png", "width": 713, "height": 861, "block_count": 1,
            "text": "２割回復かぁ",
            "blocks": [{"bbox": [608, 54, 636, 164], "language": "ja", "vertical": True,
                        "text": "２割回復かぁ",
                        "lines": [{"polygon": [[608, 54]], "text": "２割回復かぁ"}]}],
        }
        data = PageData.from_dict(legacy)
        self.assertEqual(data.blocks[0].source, "２割回復かぁ")
        self.assertEqual(data.blocks[0].translation, "")
        self.assertFalse(data.blocks[0].reviewed)

    def test_set_source_marks_edited_only_on_change(self):
        block = BlockData(bbox=[0, 0, 1, 1], source="abc")
        self.assertFalse(block.set_source("  abc "))
        self.assertFalse(block.source_edited)
        self.assertTrue(block.set_source("abd"))
        self.assertTrue(block.source_edited)

    def test_status_progression(self):
        a = BlockData(bbox=[0, 0, 1, 1], source="あ")
        b = BlockData(bbox=[0, 0, 1, 1], source="い")
        empty = BlockData(bbox=[0, 0, 1, 1], source="")  # ignored by status
        page = PageData(1, 1, [a, b, empty])
        self.assertEqual(page.status(), STATUS_RECOGNIZED)
        a.translation = "啊"
        self.assertEqual(page.status(), STATUS_RECOGNIZED)  # b still untranslated
        b.translation = "咿"
        self.assertEqual(page.status(), STATUS_TRANSLATED)
        a.reviewed = True
        self.assertEqual(page.status(), STATUS_TRANSLATED)
        b.reviewed = True
        self.assertEqual(page.status(), STATUS_REVIEWED)

    def test_page_with_no_text_is_just_recognized(self):
        self.assertEqual(PageData(1, 1, []).status(), STATUS_RECOGNIZED)


class MachineTranslationRulesTests(unittest.TestCase):
    """Automatic translation must never overwrite what the user worked on."""

    def _block(self, **kw):
        return BlockData(bbox=[0, 0, 1, 1], source=kw.pop("source", "あ"), **kw)

    def test_plain_blocks_are_translated(self):
        self.assertTrue(self._block().wants_machine_translation())

    def test_blocks_without_text_are_skipped_even_when_forced(self):
        self.assertFalse(self._block(source="").wants_machine_translation())
        self.assertFalse(self._block(source="").wants_machine_translation(force=True))

    def test_reviewed_and_hand_edited_blocks_are_protected(self):
        self.assertFalse(self._block(reviewed=True).wants_machine_translation())
        self.assertFalse(self._block(translation_edited=True).wants_machine_translation())

    def test_force_overrides_the_protection(self):
        self.assertTrue(self._block(reviewed=True).wants_machine_translation(force=True))
        self.assertTrue(self._block(translation_edited=True).wants_machine_translation(force=True))

    def test_typing_a_translation_marks_it_edited_and_clearing_hands_it_back(self):
        block = self._block()
        self.assertTrue(block.edit_translation("你好"))
        self.assertTrue(block.translation_edited)
        self.assertFalse(block.wants_machine_translation())
        block.edit_translation("")
        self.assertFalse(block.translation_edited)
        self.assertTrue(block.wants_machine_translation())

    def test_retyping_identical_text_changes_nothing(self):
        block = self._block(translation="你好")
        self.assertFalse(block.edit_translation("你好"))

    def test_machine_translation_is_not_counted_as_a_user_edit(self):
        block = self._block(translation_edited=True, translation="手改")
        self.assertTrue(block.apply_machine_translation("机翻"))
        self.assertEqual((block.translation, block.translation_edited), ("机翻", False))

    def test_machine_items_lists_only_eligible_blocks_with_their_index(self):
        page = PageData(1, 1, [
            self._block(source="一"),                                  # eligible
            self._block(source="二", reviewed=True),                   # protected
            self._block(source=""),                                    # no text
            self._block(source="四", translation="已有"),               # eligible, has translation
            self._block(source="五", translation_edited=True),          # protected
        ])
        self.assertEqual(page.machine_items(), [(0, "一"), (3, "四")])
        self.assertEqual(page.machine_items(only_missing=True), [(0, "一")])

    def test_fully_translated_ignores_blocks_without_text(self):
        page = PageData(1, 1, [self._block(source="一", translation="1"), self._block(source="")])
        self.assertTrue(page.fully_translated())
        page.blocks.append(self._block(source="三"))
        self.assertFalse(page.fully_translated())

    def test_new_flag_survives_a_round_trip_and_defaults_off_for_old_files(self):
        block = self._block()
        block.edit_translation("手改")
        again = BlockData.from_dict(json.loads(json.dumps(block.to_dict())))
        self.assertTrue(again.translation_edited)
        legacy = block.to_dict()
        del legacy["translation_edited"]                               # file from before this field
        self.assertFalse(BlockData.from_dict(legacy).translation_edited)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Project.create(Path(self._tmp.name) / "p")
        self.project.add_image_bytes(PNG, ".png", "a.png")

    def test_pending_until_saved(self):
        self.assertEqual(self.project.page_status("001"), STATUS_PENDING)
        self.project.save_page("001", PageData.from_result(_fake_result()))
        self.assertEqual(self.project.page_status("001"), STATUS_RECOGNIZED)

    def test_edits_survive_reopen(self):
        data = PageData.from_result(_fake_result())
        data.blocks[0].set_translation("哈哈")
        self.project.save_page("001", data)
        reopened = Project.open(self.project.folder)
        self.assertEqual(reopened.load_page("001").blocks[0].translation, "哈哈")

    def test_corrupt_page_file_reads_as_unrecognized(self):
        self.project.data_path("001").write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.project.load_page("001"))

    def test_no_temp_files_left_behind(self):
        self.project.save_page("001", PageData(1, 1))
        leftovers = list(self.project.folder.rglob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_status_counts(self):
        self.project.add_image_bytes(PNG)
        self.project.save_page("001", PageData.from_result(_fake_result()))
        counts = self.project.status_counts()
        self.assertEqual(counts[STATUS_PENDING], 1)
        self.assertEqual(counts[STATUS_RECOGNIZED], 1)

    def test_export_text_includes_translation(self):
        data = PageData.from_result(_fake_result())
        data.blocks[0].set_translation("哈哈")
        self.project.save_page("001", data)
        out = Path(self._tmp.name) / "out.txt"
        self.assertEqual(self.project.export_text(out), 1)
        text = out.read_text(encoding="utf-8")
        self.assertIn("ふは", text)
        self.assertIn("哈哈", text)


if __name__ == "__main__":
    unittest.main()
