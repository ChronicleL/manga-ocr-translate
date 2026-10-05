"""Japanese manga text extraction.

Detects text regions in manga page images (right-to-left, top-to-bottom reading
order) and reads the Japanese text with an OCR model.
"""

from .detector import ComicTextDetector, PageDetection
from .reading_order import TextBlock, TextLine, group_text_lines
from .translation import TranslationConfig, TranslationError
from .typeset import TypesetError, build_translations_for_blocks, render_translated_image

__all__ = [
    "ComicTextDetector",
    "PageDetection",
    "TextBlock",
    "TextLine",
    "group_text_lines",
    "TranslationConfig",
    "TranslationError",
    "TypesetError",
    "build_translations_for_blocks",
    "render_translated_image",
]

__version__ = "0.1.0"
