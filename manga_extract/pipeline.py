"""End-to-end manga text extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .detector import ComicTextDetector, PageDetection
from .image_io import imread
from .reading_order import TextBlock, group_text_lines


@dataclass
class ExtractedLine:
    """A recognized text line."""

    polygon: List[List[float]]
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"polygon": self.polygon, "text": self.text}


@dataclass
class ExtractedBlock:
    """A recognized text block in reading order."""

    xyxy: List[int]
    language: str
    vertical: bool
    lines: List[ExtractedLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(line.text for line in self.lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bbox": self.xyxy,
            "language": self.language,
            "vertical": self.vertical,
            "text": self.text,
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass
class PageResult:
    """Extraction result for one page."""

    source: str
    width: int
    height: int
    blocks: List[ExtractedBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Full page text, one block per line, in reading order."""
        return "\n".join(block.text for block in self.blocks if block.text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "block_count": len(self.blocks),
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
        }


def crop_polygon_region(
    image: np.ndarray,
    polygon: np.ndarray,
    pad_ratio: float = 0.12,
    min_pad: int = 2,
) -> Optional[np.ndarray]:
    """Crop the axis-aligned region around a text-line polygon."""
    poly = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
    x1, y1 = float(poly[:, 0].min()), float(poly[:, 1].min())
    x2, y2 = float(poly[:, 0].max()), float(poly[:, 1].max())
    w, h = x2 - x1, y2 - y1
    px = max(int(round(w * pad_ratio)), min_pad)
    py = max(int(round(h * pad_ratio)), min_pad)

    top = max(0, int(np.floor(y1)) - py)
    left = max(0, int(np.floor(x1)) - px)
    bottom = min(image.shape[0], int(np.ceil(y2)) + py)
    right = min(image.shape[1], int(np.ceil(x2)) + px)
    if bottom <= top or right <= left:
        return None
    return image[top:bottom, left:right]


def _recognize_all(recognizer: Any, crops: Sequence[np.ndarray],
                   on_progress=None) -> List[str]:
    """Recognize crops in one go when the recognizer supports batching."""
    if hasattr(recognizer, "recognize_many"):
        return list(map(str, recognizer.recognize_many(crops, on_progress=on_progress)))
    results = []
    total = len(crops)
    for i, crop in enumerate(crops):
        results.append(str(recognizer.recognize(crop)))
        if on_progress:
            on_progress(i + 1, total)
    return results


class MangaExtractor:
    """Detect, order and read Japanese text from manga page images."""

    def __init__(
        self,
        detector: Optional[ComicTextDetector] = None,
        recognizer: Optional[Any] = None,
        model_path: str | Path = "models/comic-text-detector.onnx",
        channel_order: str = "bgr",
    ) -> None:
        if detector is None:
            detector = ComicTextDetector(model_path, channel_order=channel_order)
        self.detector = detector
        self._recognizer = recognizer

    def load_recognizer(self):
        """Create the default OCR recognizer on demand."""
        if self._recognizer is None:
            from .recognizer import MangaOcrRecognizer

            self._recognizer = MangaOcrRecognizer()
        return self._recognizer

    def detect(self, image: np.ndarray) -> PageDetection:
        return self.detector.detect(image)

    def extract_blocks(self, image: np.ndarray) -> tuple[PageDetection, List[TextBlock]]:
        """Detect text and return it grouped into reading-order blocks."""
        detection = self.detector.detect(image)
        blocks = group_text_lines(
            detection.blocks,
            detection.lines,
            detection.line_scores,
            detection.mask,
            detection.width,
            detection.height,
        )
        return detection, blocks

    def extract_page(self, image: np.ndarray, source: str = "",
                     on_progress=None) -> PageResult:
        """Run the full pipeline (detection + ordering + OCR) on an image.

        ``on_progress``, if given, is called with ``(stage, done, total)``
        where *stage* is ``"detect"`` (once, after detection) or ``"ocr"``
        (repeatedly, after each recognition batch):

        * ``("detect", n_blocks, n_lines)`` – detection finished; n_blocks text
          blocks and n_lines text lines were found.
        * ``("ocr", done, total)``           – *done* of *total* lines recognised.
        """
        detection, blocks = self.extract_blocks(image)

        recognizer = self._recognizer
        result_blocks: List[ExtractedBlock] = []
        slots: List[Tuple[int, int]] = []  # (block index, line index)
        crops: List[np.ndarray] = []
        for block in blocks:
            extracted = ExtractedBlock(
                xyxy=list(block.xyxy),
                language=block.language,
                vertical=block.vertical,
            )
            for polygon in block.lines:
                extracted.lines.append(
                    ExtractedLine(
                        polygon=[[float(x), float(y)] for x, y in polygon],
                        text="",
                    )
                )
                if recognizer is not None:
                    crop = crop_polygon_region(image, polygon)
                    if crop is not None:
                        slots.append((len(result_blocks), len(extracted.lines) - 1))
                        crops.append(crop)
            result_blocks.append(extracted)

        if on_progress:
            on_progress("detect", len(blocks), len(crops))

        # Every crop goes through the recognizer at once so a batching
        # recognizer can fill a single forward pass instead of one per line.
        if recognizer is not None and crops:
            ocr_cb = (lambda d, t: on_progress("ocr", d, t)) if on_progress else None
            for (block_index, line_index), text in zip(
                slots, _recognize_all(recognizer, crops, on_progress=ocr_cb)
            ):
                result_blocks[block_index].lines[line_index].text = text

        return PageResult(
            source=source,
            width=detection.width,
            height=detection.height,
            blocks=result_blocks,
        )

    def redetect_region(self, image: np.ndarray, xyxy: Sequence[int]) -> "ExtractedBlock":
        """Re-detect and OCR within a user-supplied rectangle.

        Detection runs on the cropped sub-image with looser thresholds (the letterbox
        zoom improves recall on small text). If nothing is detected, the whole rectangle
        is treated as a single text line.  Always returns an ``ExtractedBlock`` (text may
        be empty if no recognizer is loaded or the crop is blank).
        """
        x1, y1, x2, y2 = (int(v) for v in xyxy)
        h, w = image.shape[:2]
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(w, x2); y2 = min(h, y2)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return ExtractedBlock(xyxy=[x1, y1, x2, y2], language="unknown", vertical=False)

        # Detect on the crop with looser thresholds for better recall.
        old_conf = self.detector.conf_thresh
        old_box  = self.detector.box_score_thresh
        self.detector.conf_thresh      = 0.2
        self.detector.box_score_thresh = 0.4
        try:
            detection = self.detector.detect(crop)
        finally:
            self.detector.conf_thresh      = old_conf
            self.detector.box_score_thresh = old_box

        ch, cw = crop.shape[:2]
        sub_blocks = group_text_lines(
            detection.blocks, detection.lines, detection.line_scores,
            detection.mask, cw, ch,
        )

        if sub_blocks:
            primary = sub_blocks[0]
            result_xyxy = [
                int(primary.xyxy[0]) + x1, int(primary.xyxy[1]) + y1,
                int(primary.xyxy[2]) + x1, int(primary.xyxy[3]) + y1,
            ]
            vertical = primary.vertical
            line_polys: List[List[List[float]]] = []
            for blk in sub_blocks:
                for poly in blk.lines:
                    line_polys.append([[float(p[0]) + x1, float(p[1]) + y1] for p in poly])
        else:
            # Fallback: treat the entire rectangle as one line.
            result_xyxy = [x1, y1, x2, y2]
            vertical = False
            line_polys = [[[float(x1), float(y1)], [float(x2), float(y1)],
                           [float(x2), float(y2)], [float(x1), float(y2)]]]

        extracted = ExtractedBlock(xyxy=result_xyxy, language="ja", vertical=vertical)

        recognizer = self._recognizer
        if recognizer is not None and line_polys:
            crops = [crop_polygon_region(image, poly) for poly in line_polys]
            crops = [c for c in crops if c is not None]
            if crops:
                texts = _recognize_all(recognizer, crops)
                for poly, text in zip(line_polys, texts):
                    extracted.lines.append(ExtractedLine(
                        polygon=poly,
                        text=text,
                    ))

        return extracted

    def extract_file(self, path: str | Path) -> PageResult:
        path = Path(path)
        image = imread(path)
        return self.extract_page(image, source=str(path))
