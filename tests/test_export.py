"""export: chapter / page images, naming, formats, cancelling, failures; JSON and text."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_extract.studio.export import (  # noqa: E402
    ExportOptions,
    export_chapter,
    export_page_image,
    file_name,
)
from manga_extract.studio.project import BlockData, PageData, Project  # noqa: E402

SAMPLES = [ROOT / "samples" / "1.png", ROOT / "samples" / "2.png"]
HAVE_SAMPLES = all(p.is_file() for p in SAMPLES)


def _font_available() -> bool:
    try:
        from manga_extract.typeset import find_cjk_font

        return find_cjk_font() is not None
    except Exception:
        return False


HAVE_FONT = _font_available()


def pixels(path):
    """(digest of the pixels, size). A digest, not the raw bytes: when two images differ,
    unittest would otherwise try to diff megabytes of pixel data and appear to hang."""
    import hashlib

    from PIL import Image

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return hashlib.sha1(rgb.tobytes()).hexdigest(), image.size


def translated_page(width: int, height: int) -> PageData:
    return PageData(width, height, [
        BlockData(bbox=[500, 50, 560, 300], vertical=True, source="ふはは", translation="哈哈哈"),
        BlockData(bbox=[100, 600, 300, 680], source="こんにちは", translation="你好"),
    ])


def untranslated_page(width: int, height: int) -> PageData:
    return PageData(width, height, [BlockData(bbox=[100, 600, 300, 680], source="こんにちは")])


@unittest.skipUnless(HAVE_SAMPLES, "sample images missing")
class ExportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="export_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = Project.create(self.tmp / "p")
        self.out = self.tmp / "out"

    def add_pages(self, translated=(True, False, False)):
        """Page 1 / 2 are the sample pages, page 3 a small generated PNG (never recognised)."""
        from PIL import Image

        extra = self.tmp / "extra.png"
        Image.new("RGB", (200, 300), "#88aa66").save(extra)
        self.project.add_image_files([SAMPLES[0], SAMPLES[1], extra])
        self.ids = [p.id for p in self.project.pages]
        if translated[0]:
            self.project.save_page(self.ids[0], translated_page(713, 861))
        if translated[1]:
            self.project.save_page(self.ids[1], translated_page(716, 891))
        elif translated[1] is False:
            self.project.save_page(self.ids[1], untranslated_page(716, 891))
        return self.project

    def original(self, index):
        return pixels(self.project.image_path(self.ids[index]))


class NamingTests(unittest.TestCase):
    def test_names_are_zero_padded_to_the_page_count(self):
        self.assertEqual(file_name(1, 24, "png"), "001.png")
        self.assertEqual(file_name(24, 24, "jpg"), "024.jpg")
        self.assertEqual(file_name(7, 1000, "png"), "0007.png")
        self.assertEqual(file_name(1000, 1000, "png"), "1000.png")

    def test_options_are_validated(self):
        with self.assertRaises(ValueError):
            ExportOptions(Path("x"), fmt="gif")
        self.assertEqual(ExportOptions(Path("x"), quality=500).quality, 100)
        self.assertEqual(ExportOptions(Path("x"), quality=-3).quality, 1)


class ChapterExportTests(ExportBase):
    def test_untranslated_pages_are_copied_as_they_are_by_default(self):
        self.add_pages()
        report = export_chapter(self.project, ExportOptions(self.out))
        self.assertTrue(report.ok if HAVE_FONT else True)
        names = sorted(p.name for p in self.out.iterdir())
        if HAVE_FONT:
            self.assertEqual(names, ["001.png", "002.png", "003.png"])
            self.assertEqual(pixels(self.out / "002.png"), self.original(1))      # untouched
            self.assertEqual(pixels(self.out / "003.png"), self.original(2))      # never recognised
        else:
            self.assertIn("002.png", names)

    @unittest.skipUnless(HAVE_FONT, "no CJK font")
    def test_a_translated_page_differs_from_its_original_but_keeps_its_size(self):
        self.add_pages()
        export_chapter(self.project, ExportOptions(self.out))
        data, size = pixels(self.out / "001.png")
        original_data, original_size = self.original(0)
        self.assertEqual(size, original_size)
        self.assertNotEqual(data, original_data)

    def test_pages_without_a_translation_can_be_left_out_but_keep_their_numbers(self):
        self.add_pages(translated=(False, True, False))                            # only page 2 is done
        if not HAVE_FONT:
            self.skipTest("no CJK font")
        report = export_chapter(self.project, ExportOptions(self.out, include_untranslated=False))
        self.assertEqual([p.name for p in report.written], ["002.png"])             # position, not a counter
        self.assertEqual(report.skipped, 2)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["002.png"])

    def test_numbers_follow_the_page_order_of_the_project(self):
        self.add_pages(translated=(False, False, False))
        self.project.reorder([self.ids[2], self.ids[0], self.ids[1]])
        export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual(pixels(self.out / "001.png"), self.original(2))            # the generated page first
        self.assertEqual(pixels(self.out / "002.png"), self.original(0))
        self.assertEqual(pixels(self.out / "003.png"), self.original(1))

    def test_jpeg_output_is_really_jpeg_and_quality_changes_the_size(self):
        self.add_pages(translated=(False, False, False))
        sizes = {}
        for quality in (95, 40):
            folder = self.tmp / f"q{quality}"
            export_chapter(self.project, ExportOptions(folder, fmt="jpg", quality=quality))
            first = folder / "001.jpg"
            self.assertEqual(first.read_bytes()[:3], b"\xff\xd8\xff")
            sizes[quality] = first.stat().st_size
        self.assertGreater(sizes[95], sizes[40])

    def test_the_output_folder_is_created_and_existing_files_are_replaced(self):
        self.add_pages(translated=(False, False, False))
        nested = self.tmp / "a" / "b" / "out"
        (nested).mkdir(parents=True)
        (nested / "001.png").write_bytes(b"old")
        export_chapter(self.project, ExportOptions(nested))
        self.assertEqual(pixels(nested / "001.png"), self.original(0))

    def test_an_empty_project_exports_nothing_without_failing(self):
        report = export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual((report.written, report.failed, report.cancelled), ([], [], False))
        self.assertTrue(self.out.is_dir())

    def test_images_with_alpha_palette_or_grey_levels_are_handled(self):
        from PIL import Image

        for mode in ("RGBA", "P", "L", "LA"):
            path = self.tmp / f"{mode}.png"
            Image.new(mode, (60, 80)).save(path)
            self.project.add_image_files([path])
        report = export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual(report.failed, [])
        self.assertEqual(len(report.written), 4)
        self.assertTrue(all(pixels(p)[1] == (60, 80) for p in report.written))

    def test_progress_is_reported_after_every_page(self):
        self.add_pages(translated=(False, False, False))
        calls = []
        export_chapter(self.project, ExportOptions(self.out), progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])

    def test_cancelling_stops_before_the_next_page(self):
        self.add_pages(translated=(False, False, False))
        cancel = threading.Event()
        report = export_chapter(self.project, ExportOptions(self.out),
                                progress=lambda d, t: cancel.set() if d == 1 else None, cancel=cancel)
        self.assertTrue(report.cancelled)
        self.assertEqual([p.name for p in report.written], ["001.png"])
        self.assertFalse(report.ok)

    def test_cancelling_before_the_start_writes_nothing(self):
        self.add_pages(translated=(False, False, False))
        cancel = threading.Event()
        cancel.set()
        report = export_chapter(self.project, ExportOptions(self.out), cancel=cancel)
        self.assertEqual((report.written, report.cancelled), ([], True))

    def test_one_unreadable_page_is_reported_and_the_others_are_still_written(self):
        self.add_pages(translated=(False, False, False))
        self.project.image_path(self.ids[1]).write_bytes(b"not an image at all")
        report = export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual([position for position, _msg in report.failed], [2])
        self.assertEqual(sorted(p.name for p in report.written), ["001.png", "003.png"])
        self.assertFalse(report.ok)

    def test_a_page_whose_file_vanished_is_reported_too(self):
        self.add_pages(translated=(False, False, False))
        self.project.image_path(self.ids[0]).unlink()
        report = export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual([position for position, _msg in report.failed], [1])
        self.assertEqual(len(report.written), 2)

    def test_a_missing_font_fails_only_the_translated_pages_and_says_why(self):
        self.add_pages()
        with mock.patch("manga_extract.typeset.find_cjk_font", return_value=None):
            report = export_chapter(self.project, ExportOptions(self.out))
        self.assertEqual([position for position, _msg in report.failed], [1])
        self.assertIn("字体", report.failed[0][1])
        self.assertEqual(sorted(p.name for p in report.written), ["002.png", "003.png"])


class PageExportTests(ExportBase):
    @unittest.skipUnless(HAVE_FONT, "no CJK font")
    def test_a_translated_page_is_exported_with_its_translation(self):
        self.add_pages()
        target = self.tmp / "one" / "page.png"
        export_page_image(self.project, self.ids[0], target)
        self.assertNotEqual(pixels(target)[0], self.original(0)[0])

    def test_an_untranslated_page_is_exported_as_it_is(self):
        self.add_pages()
        target = self.tmp / "page2.png"
        export_page_image(self.project, self.ids[1], target)
        self.assertEqual(pixels(target), self.original(1))

    def test_the_extension_picks_the_format(self):
        self.add_pages()
        for name, magic in (("a.jpg", b"\xff\xd8\xff"), ("b.JPEG", b"\xff\xd8\xff"), ("c.png", b"\x89PNG")):
            target = self.tmp / name
            export_page_image(self.project, self.ids[1], target)
            self.assertEqual(target.read_bytes()[:len(magic)], magic, name)


class JsonAndTextTests(ExportBase):
    def test_json_holds_every_page_with_its_blocks_and_flags(self):
        self.add_pages(translated=(True, False, False))                          # page 3 never recognised
        self.project.entry(self.ids[0])
        data = self.project.load_page(self.ids[0])
        data.blocks[0].reviewed = True
        data.blocks[1].edit_translation("手写")
        self.project.save_page(self.ids[0], data)
        path = self.tmp / "project.json.export"
        self.assertEqual(self.project.export_json(path), 3)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((doc["version"], doc["name"], doc["target_lang"]), (1, "p", "zh-CN"))
        self.assertEqual([p["index"] for p in doc["pages"]], [1, 2, 3])
        first, second, third = doc["pages"]
        self.assertEqual((first["recognized"], len(first["blocks"])), (True, 2))
        self.assertEqual((first["width"], first["height"]), (713, 861))
        self.assertTrue(first["blocks"][0]["reviewed"])
        self.assertTrue(first["blocks"][1]["translation_edited"])
        self.assertEqual(first["blocks"][0]["translation"], "哈哈哈")
        self.assertEqual((third["recognized"], third["blocks"]), (False, []))     # never recognised
        self.assertEqual(first["origin"], "1.png")

    def test_json_of_an_empty_project(self):
        path = self.tmp / "empty.json"
        self.assertEqual(self.project.export_json(path), 0)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pages"], [])

    def test_text_export_pairs_original_and_translation_in_page_order(self):
        self.add_pages(translated=(True, False, False))
        path = self.tmp / "out.txt"
        count = self.project.export_text(path)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(count, 3)                                               # 2 + 1 blocks with text
        self.assertLess(text.index("ふはは"), text.index("哈哈哈"))
        self.assertLess(text.index("第 1 页"), text.index("第 2 页"))
        only_source = self.tmp / "src.txt"
        self.project.export_text(only_source, with_translation=False)
        self.assertNotIn("哈哈哈", only_source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
