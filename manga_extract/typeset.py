"""Render translations back onto a page image ("译文替换排版" preview).

Each text block is covered with its local background colour and the translated
text is drawn inside the block box. Vertical blocks are typeset vertically
(right-to-left columns), horizontal blocks are wrapped horizontally.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Preferred CJK fonts, in order. ``(path, ttc_index)``.
_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),      # 微软雅黑
    (r"C:\Windows\Fonts\simhei.ttf", 0),    # 黑体
    (r"C:\Windows\Fonts\msjh.ttc", 0),      # 微軟正黑體
    (r"C:\Windows\Fonts\NotoSansSC-VF.ttf", 0),
    (r"C:\Windows\Fonts\YuGothM.ttc", 0),   # 游ゴシック
    (r"C:\Windows\Fonts\simsun.ttc", 0),    # 宋体
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
]

MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 48


class TypesetError(RuntimeError):
    """Raised when the translation cannot be typeset."""


def find_cjk_font() -> Optional[tuple]:
    """Return ``(path, index)`` of the first available CJK font, or None."""
    override = os.environ.get("MANGA_EXTRACT_FONT")
    if override:
        path = Path(override)
        if path.is_file():
            return str(path), 0
    for path, index in _FONT_CANDIDATES:
        if Path(path).is_file():
            return path, index
    return None


_default_style = {"font_path": "", "font_scale": 1.0}


def set_default_style(font_path: str = "", font_scale: float = 1.0) -> None:
    """User-chosen font and size scale, used when a caller does not pass its own."""
    _default_style["font_path"] = font_path or ""
    _default_style["font_scale"] = min(3.0, max(0.3, float(font_scale or 1.0)))


def _default_font() -> Optional[tuple]:
    path = _default_style["font_path"]
    return (path, 0) if path and Path(path).is_file() else None


def build_translations_for_blocks(blocks: Sequence, block_pairs: Iterable[tuple]) -> List[str]:
    """Align ``(block_index, src, tgt)`` pairs with a block list."""
    texts = [""] * len(blocks)
    for index, _source, target in block_pairs:
        if 0 <= index < len(texts):
            texts[index] = target
    return texts


def _cover_color(image_bgr: np.ndarray, box: Sequence[int]) -> tuple:
    x1, y1, x2, y2 = box
    region = image_bgr[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
    if region.size == 0:
        return (255, 255, 255)
    b, g, r = (int(v) for v in np.median(region.reshape(-1, 3), axis=0))
    return (r, g, b)


def _text_color(background: tuple) -> tuple:
    r, g, b = background
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (30, 30, 30) if luminance > 140 else (245, 245, 245)


@lru_cache(maxsize=256)
def _load_font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError as exc:
        raise TypesetError(f"无法加载字体 {path}：{exc}") from exc


@lru_cache(maxsize=1)
def list_system_fonts() -> List[tuple]:
    """``[(显示名, path)]`` of installed fonts that can draw Chinese, for the settings dialog."""
    roots = [Path(r"C:\Windows\Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
             Path("/usr/share/fonts"), Path("/System/Library/Fonts")]
    found: List[tuple] = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for file in sorted(root.rglob("*")):
            if file.suffix.lower() not in (".ttf", ".otf", ".ttc") or str(file) in seen:
                continue
            seen.add(str(file))
            try:
                font = ImageFont.truetype(str(file), 24, index=0)
                if not _font_draws(font, "汉"):
                    continue
                family, style = font.getname()
            except (OSError, ValueError):
                continue
            found.append((f"{family} {style}".strip(), str(file)))
    return found


def _font_draws(font: ImageFont.FreeTypeFont, char: str) -> bool:
    """True when the font has a real glyph for ``char`` (not blank, not the missing-glyph box)."""
    shape = _glyph_image(font, char)
    return shape.getbbox() is not None and shape.tobytes() != _glyph_image(font, "\uffff").tobytes()


def _glyph_image(font: ImageFont.FreeTypeFont, char: str) -> Image.Image:
    image = Image.new("L", (96, 96), 0)
    ImageDraw.Draw(image).text((16, 16), char, font=font, fill=255)
    return image


@lru_cache(maxsize=1024)
def _has_glyph(path: str, index: int, char: str) -> bool:
    return _font_draws(_load_font(path, index, 32), char)


# 竖排时需要特殊处理的字符
_ROTATE = set("…⋯—―─–～~-")                 # 横向符号：转 90° 居中
_CORNER = set("，。、．,.")                  # 小标点：靠格子右上角
_NO_START = set("，。、．,.！？!?；：;:）」』】》〉)]”’")   # 避头：不放在一列的开头
_VERTICAL_FORMS = {                          # 优先使用字体里的竖排字形
    "「": "﹁", "」": "﹂", "『": "﹃", "』": "﹄", "（": "︵", "）": "︶", "(": "︵", ")": "︶",
    "【": "︻", "】": "︼", "《": "︽", "》": "︾", "〈": "︿", "〉": "﹀", "[": "﹇", "]": "﹈",
}
_NEWLINE = None
_RUN = re.compile(r"[0-9A-Za-z]+")


def _tokenize_vertical(path: str, index: int, text: str) -> list:
    """Split ``text`` into cells ``(char, mode, no_start)``; ``None`` is a forced column break."""
    cells: list = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        run = _RUN.match(text, pos)
        if char == "\n":
            cells.append(_NEWLINE)
        elif run:
            if len(run.group()) <= 2:                   # 短数字 / 字母横排放进一格
                cells.append((run.group(), "center", False))
            else:                                       # 长的逐个竖排
                cells.extend((c, "normal", False) for c in run.group())
            pos = run.end()
            continue
        elif char in _VERTICAL_FORMS:
            form = _VERTICAL_FORMS[char]
            if _has_glyph(path, index, form):
                cells.append((form, "center", char in _NO_START))
            else:
                cells.append((char, "rotate", char in _NO_START))
        elif char in _ROTATE:
            cells.append((char, "rotate", False))
        elif char in _CORNER:
            cells.append((char, "corner", True))
        else:
            cells.append((char, "normal", char in _NO_START))
        pos += 1
    return cells


def _layout_columns(cells: list, per_column: int) -> list:
    """Fill columns of ``per_column`` cells; punctuation never starts a column."""
    columns: list = [[]]
    for cell in cells:
        if cell is _NEWLINE:
            columns.append([])
            continue
        current = columns[-1]
        if len(current) >= per_column:
            fresh: list = []
            columns.append(fresh)
            if cell[2] and len(current) > 1:
                fresh.append(current.pop())             # 把上一列最后一个字带下来
            current = fresh
        current.append(cell)
    return [column for column in columns if column]


def _wrap_horizontal(font: ImageFont.FreeTypeFont, text: str, max_width: float) -> List[str]:
    lines: List[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if not current or font.getlength(current + char) <= max_width:
            current += char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _sizes(block_size: int, max_size: int) -> tuple:
    """Candidate font sizes, largest first, and whether the size is fixed by the user."""
    if block_size > 0:
        return [block_size], True
    return list(range(max(max_size, MIN_FONT_SIZE), MIN_FONT_SIZE - 1, -1)), False


def _fit_vertical(path, index, text, width, height, padding, sizes, fixed=False):
    usable_w = width - 2 * padding
    usable_h = height - 2 * padding
    cells = _tokenize_vertical(path, index, text)
    result = None
    for size in sizes:
        font = _load_font(path, index, size)
        advance = font.getlength("汉") or size
        line_height = size * 1.06
        per_column = int(usable_h // line_height)
        if per_column < 1 or advance > usable_w:
            if fixed:
                result = (font, _layout_columns(cells, max(1, per_column)), advance, line_height)
            continue
        columns = _layout_columns(cells, per_column)
        result = (font, columns, advance, line_height)
        if len(columns) * advance <= usable_w:
            return result
    return result if fixed else None        # 用户指定字号时即使放不下也照画


def _draw_cell(canvas, draw, cell, left, top, font, advance, line_height, color):
    char, mode, _no_start = cell
    if mode == "normal":
        draw.text((left, top), char, font=font, fill=color)
        return
    pad = int(max(advance, line_height))
    tmp = Image.new("RGBA", (2 * pad + int(advance) + 1, 2 * pad + int(line_height) + 1), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((pad, pad), char, font=font, fill=color + (255,))
    ink = tmp.getchannel("A").getbbox()
    if ink is None:
        return
    glyph = tmp.crop(ink)
    if mode == "rotate":
        glyph = glyph.rotate(-90, expand=True)           # 顺时针 90°，横线变竖线
    if mode == "corner":
        x = left + advance - glyph.width - advance * 0.1
        y = top + line_height * 0.08
    else:
        x = left + (advance - glyph.width) / 2
        y = top + (line_height - glyph.height) / 2
    canvas.alpha_composite(glyph, (int(round(x)), int(round(y))))


def _draw_vertical(canvas, draw, columns, box, font, advance, line_height, color):
    x1, y1, x2, y2 = box
    for column, cells in enumerate(columns):
        left = x2 - (column + 1) * advance
        for row, cell in enumerate(cells):
            _draw_cell(canvas, draw, cell, left, y1 + row * line_height,
                       font, advance, line_height, color)


def _fit_horizontal(path, index, text, width, height, padding, sizes, fixed=False):
    usable_w = width - 2 * padding
    usable_h = height - 2 * padding
    result = None
    for size in sizes:
        font = _load_font(path, index, size)
        line_height = size * 1.25
        lines = _wrap_horizontal(font, text, usable_w)
        result = (font, lines, line_height)
        if len(lines) * line_height <= usable_h and all(
            font.getlength(line) <= usable_w for line in lines
        ):
            return result
    return result if fixed else None


def _draw_horizontal(draw, lines, box, font, line_height, color):
    x1, y1, x2, y2 = box
    block_height = len(lines) * line_height
    top = y1 + max(0.0, ((y2 - y1) - block_height) / 2)
    for i, line in enumerate(lines):
        line_width = font.getlength(line)
        left = x1 + max(0.0, ((x2 - x1) - line_width) / 2)
        draw.text((left, top + i * line_height), line, font=font, fill=color)


def render_translated_image(
    image_bgr: np.ndarray,
    blocks: Sequence,
    translations: Sequence[str],
    font_path: Optional[tuple] = None,
    cover_alpha: int = 235,
    font_scale: Optional[float] = None,
) -> Image.Image:
    """Return an RGB preview with translations drawn over the original.

    ``blocks`` are :class:`~manga_extract.pipeline.ExtractedBlock` objects and
    ``translations`` is a list of the same length (empty entries are skipped).
    ``font_scale`` scales the upper bound of the automatic size; a block with a
    positive ``font_size`` attribute is drawn at exactly that size.
    """
    if font_scale is None:
        font_scale = _default_style["font_scale"]
    max_size = max(MIN_FONT_SIZE, int(round(MAX_FONT_SIZE * font_scale)))
    if len(translations) < len(blocks):
        translations = list(translations) + [""] * (len(blocks) - len(translations))

    font_path = font_path or _default_font() or find_cjk_font()
    if font_path is None:
        raise TypesetError(
            "没有找到可用的中文字体，请用环境变量 MANGA_EXTRACT_FONT 指定一个 .ttf/.ttc 字体文件"
        )
    path, index = font_path

    canvas = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    for block, text in zip(blocks, translations):
        text = (text or "").strip()
        if not text:
            continue
        x1, y1, x2, y2 = (int(v) for v in block.xyxy)
        width, height = x2 - x1, y2 - y1
        if width < 6 or height < 6:
            continue

        background = _cover_color(image_bgr, (x1, y1, x2, y2))
        draw.rectangle([x1, y1, x2, y2], fill=background + (cover_alpha,))
        color = _text_color(background)
        padding = max(2, int(min(width, height) * 0.06))
        box = (x1 + padding, y1 + padding, x2 - padding, y2 - padding)

        sizes, fixed = _sizes(int(getattr(block, "font_size", 0) or 0), max_size)
        if block.vertical:
            fitted = _fit_vertical(path, index, text, width, height, padding, sizes, fixed)
            if fitted is not None:
                font, columns, advance, line_height = fitted
                _draw_vertical(canvas, draw, columns, box, font, advance, line_height, color)
                continue

        fitted = _fit_horizontal(path, index, text, width, height, padding, sizes, fixed)
        if fitted is not None:
            font, lines, line_height = fitted
            _draw_horizontal(draw, lines, box, font, line_height, color)

    return canvas.convert("RGB")
