"""Render translations back onto a page image ("译文替换排版" preview).

Each text block is covered with its local background colour and the translated
text is drawn inside the block box. Vertical blocks are typeset vertically
(right-to-left columns), horizontal blocks are wrapped horizontally.
"""

from __future__ import annotations

import math
import os
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


def _load_font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError as exc:
        raise TypesetError(f"无法加载字体 {path}：{exc}") from exc


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


def _fit_vertical(path, index, text, width, height, padding):
    usable_w = width - 2 * padding
    usable_h = height - 2 * padding
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -1):
        font = _load_font(path, index, size)
        advance = font.getlength("汉") or size
        line_height = size * 1.06
        per_column = int(usable_h // line_height)
        if per_column < 1 or advance > usable_w:
            continue
        columns = math.ceil(len(text) / per_column)
        if columns * advance <= usable_w:
            return font, per_column, advance, line_height
    return None


def _draw_vertical(draw, text, box, font, per_column, advance, line_height, color):
    x1, y1, x2, y2 = box
    for column in range(math.ceil(len(text) / per_column)):
        left = x2 - (column + 1) * advance
        for row in range(per_column):
            i = column * per_column + row
            if i >= len(text):
                return
            draw.text((left, y1 + row * line_height), text[i], font=font, fill=color)


def _fit_horizontal(path, index, text, width, height, padding):
    usable_w = width - 2 * padding
    usable_h = height - 2 * padding
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -1):
        font = _load_font(path, index, size)
        line_height = size * 1.25
        lines = _wrap_horizontal(font, text, usable_w)
        if len(lines) * line_height <= usable_h and all(
            font.getlength(line) <= usable_w for line in lines
        ):
            return font, lines, line_height
    return None


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
) -> Image.Image:
    """Return an RGB preview with translations drawn over the original.

    ``blocks`` are :class:`~manga_extract.pipeline.ExtractedBlock` objects and
    ``translations`` is a list of the same length (empty entries are skipped).
    """
    if len(translations) < len(blocks):
        translations = list(translations) + [""] * (len(blocks) - len(translations))

    font_path = font_path or find_cjk_font()
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

        if block.vertical:
            fitted = _fit_vertical(path, index, text, width, height, padding)
            if fitted is not None:
                font, per_column, advance, line_height = fitted
                _draw_vertical(draw, text, box, font, per_column, advance, line_height, color)
                continue

        fitted = _fit_horizontal(path, index, text, width, height, padding)
        if fitted is not None:
            font, lines, line_height = fitted
            _draw_horizontal(draw, lines, box, font, line_height, color)

    return canvas.convert("RGB")
