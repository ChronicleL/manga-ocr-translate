"""Renders the translated overlay of a page off the GUI thread.

``typeset.render_translated_image`` repaints the whole page and reloads fonts at
every candidate size, which is too slow to run while the user types. Requests
therefore go to a single worker; only the newest version requested for a page is
rendered, and the GUI ignores results whose version is no longer current.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .project import PageData


@dataclass
class _Box:
    """The attributes ``typeset`` reads from a block."""

    xyxy: List[int]
    vertical: bool
    font_size: int = 0


def has_translation(data: "PageData | None") -> bool:
    return data is not None and data.has_translation()


def rgb_to_qimage(width: int, height: int, rgb_bytes: bytes) -> QImage:
    """Own a copy of the pixels, so the source buffer can be freed."""
    return QImage(rgb_bytes, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


class OverlayRenderer(QObject):
    rendered = Signal(str, int, QImage)   # page id, version, overlay
    failed = Signal(str, int, str)        # page id, version, message

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="overlay")
        self._latest: Dict[str, int] = {}
        self._closed = False

    def request(self, page_id: str, version: int, image_path: str | Path,
                data: PageData) -> None:
        """Render ``data``'s translations over the page image (asynchronous)."""
        if self._closed:
            return                          # a debounce timer can still fire after shutdown
        boxes = [_Box(list(block.bbox), block.vertical, block.font_size) for block in data.blocks]
        translations = data.translations()
        self._latest[page_id] = version
        self._executor.submit(self._work, page_id, version, str(image_path), boxes, translations)

    def forget(self, page_id: str) -> None:
        self._latest.pop(page_id, None)

    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _work(self, page_id: str, version: int, image_path: str,
              boxes: Sequence[_Box], translations: Sequence[str]) -> None:
        if self._latest.get(page_id) != version:
            return                          # a newer request superseded this one
        try:
            from ..image_io import imread
            from ..typeset import render_translated_image

            image = render_translated_image(imread(image_path), boxes, translations)
            rgb = image.convert("RGB")
            qimage = rgb_to_qimage(rgb.width, rgb.height, rgb.tobytes())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.failed.emit(page_id, version, str(exc) or type(exc).__name__)
            return
        self.rendered.emit(page_id, version, qimage)
