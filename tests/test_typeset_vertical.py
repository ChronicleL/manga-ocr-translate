"""竖排译文的符号处理、字号覆盖，以及设置 / 数据里的新字段。不加载任何模型。"""

import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from manga_extract import typeset
from manga_extract.studio.config import StudioConfig
from manga_extract.studio.project import BlockData

FONT = typeset.find_cjk_font()


def _ink(image):
    """Bounding box (x1, y1, x2, y2) of the dark pixels of a white page, or None."""
    pixels = np.asarray(image.convert("L"))
    ys, xs = np.nonzero(pixels < 128)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _render(text, vertical=True, font_size=0, size=(120, 300), font_scale=None):
    page = np.full((size[1], size[0], 3), 255, np.uint8)
    block = SimpleNamespace(xyxy=[0, 0, size[0], size[1]], vertical=vertical, font_size=font_size)
    return typeset.render_translated_image(page, [block], [text], font_scale=font_scale)


class TokenizeTests(unittest.TestCase):
    path = "unused.ttf"

    def setUp(self):
        # 不依赖具体字体：假装所有竖排字形都不存在，括号回退为旋转
        patcher = mock.patch.object(typeset, "_has_glyph", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ellipsis_dash_are_rotated_one_cell_each(self):
        cells = typeset._tokenize_vertical(self.path, 0, "好……——")
        self.assertEqual([c[:2] for c in cells],
                         [("好", "normal")] + [("…", "rotate")] * 2 + [("—", "rotate")] * 2)

    def test_small_punctuation_goes_to_the_corner_and_never_starts_a_column(self):
        cells = typeset._tokenize_vertical(self.path, 0, "好。")
        self.assertEqual(cells[1][:2], ("。", "corner"))
        self.assertTrue(cells[1][2])

    def test_newline_is_a_column_break_and_short_runs_share_a_cell(self):
        cells = typeset._tokenize_vertical(self.path, 0, "a1\n2024")
        self.assertEqual(cells[0], ("a1", "center", False))
        self.assertIsNone(cells[1])
        self.assertEqual([c[0] for c in cells[2:]], list("2024"))

    def test_brackets_fall_back_to_rotation_without_vertical_glyphs(self):
        self.assertEqual(typeset._tokenize_vertical(self.path, 0, "「")[0][:2], ("「", "rotate"))


class LayoutTests(unittest.TestCase):
    def test_punctuation_is_pulled_down_with_the_previous_character(self):
        cells = [(c, "normal", c in "。") for c in "ABCD。E"]
        columns = typeset._layout_columns(cells, 4)
        self.assertEqual(["".join(c[0] for c in col) for col in columns], ["ABC", "D。E"])

    def test_newline_forces_a_new_column(self):
        cells = [("A", "normal", False), None, ("B", "normal", False)]
        self.assertEqual(len(typeset._layout_columns(cells, 10)), 2)


@unittest.skipIf(FONT is None, "需要一个已安装的中文字体")
class RenderTests(unittest.TestCase):
    def test_vertical_ellipsis_is_a_column_of_dots_not_a_horizontal_row(self):
        x1, y1, x2, y2 = _ink(_render("…"))
        self.assertGreater(y2 - y1, x2 - x1)          # 竖着排开：高大于宽

    def test_horizontal_blocks_keep_the_ellipsis_horizontal(self):
        x1, y1, x2, y2 = _ink(_render("……", vertical=False, size=(300, 120)))
        self.assertGreater(x2 - x1, y2 - y1)

    def test_brackets_render_with_the_real_font_check(self):
        self.assertIsNotNone(_ink(_render("「好」（是）")))

    def test_system_font_list_finds_fonts_that_draw_chinese(self):
        fonts = typeset.list_system_fonts()
        self.assertTrue(fonts)
        self.assertTrue(all(path for _name, path in fonts))

    def test_block_font_size_overrides_the_automatic_size(self):
        small = _ink(_render("好好", font_size=12))
        large = _ink(_render("好好", font_size=40))
        self.assertGreater(large[2] - large[0], small[2] - small[0])

    def test_font_scale_caps_the_automatic_size(self):
        full = _ink(_render("好", size=(200, 200)))
        half = _ink(_render("好", size=(200, 200), font_scale=0.5))
        self.assertLess(half[2] - half[0], full[2] - full[0])

    def test_fixed_size_is_drawn_even_when_it_does_not_fit(self):
        self.assertIsNotNone(_ink(_render("好" * 30, font_size=60, size=(60, 60))))


class FieldTests(unittest.TestCase):
    def test_block_font_size_round_trips_and_bad_values_mean_automatic(self):
        block = BlockData(bbox=[0, 0, 10, 10], font_size=24)
        self.assertEqual(BlockData.from_dict(block.to_dict()).font_size, 24)
        for bad in ("big", -3, 9999, None):
            self.assertEqual(BlockData.from_dict({"bbox": [0, 0, 1, 1], "font_size": bad}).font_size, 0)
        self.assertEqual(BlockData.from_dict({"bbox": [0, 0, 1, 1]}).font_size, 0)   # old files

    def test_config_font_fields_are_validated(self):
        config = StudioConfig.from_dict({"font_path": "C:/f.ttf", "font_scale": 1.5})
        self.assertEqual((config.font_path, config.font_scale), ("C:/f.ttf", 1.5))
        for bad in ("x", 0, 10, True):
            self.assertEqual(StudioConfig.from_dict({"font_scale": bad}).font_scale, 1.0)

    def test_default_style_is_clamped(self):
        try:
            typeset.set_default_style("", 99)
            self.assertEqual(typeset._default_style["font_scale"], 3.0)
        finally:
            typeset.set_default_style("", 1.0)


if __name__ == "__main__":
    unittest.main()
