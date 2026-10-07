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


def _recognize_all(recognizer: Any, crops: Sequence[np.ndarray]) -> List[str]:
    """Recognize crops in one go when the recognizer supports batching."""
    if hasattr(recognizer, "recognize_many"):
        return list(map(str, recognizer.recognize_many(crops)))
    return [recognizer.recognize(crop) for crop in crops]


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

    def extract_page(self, image: np.ndarray, source: str = "") -> PageResult:
        """Run the full pipeline (detection + ordering + OCR) on an image."""
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

        # Every crop goes through the recognizer at once so a batching
        # recognizer can fill a single forward pass instead of one per line.
        if recognizer is not None and crops:
            for (block_index, line_index), text in zip(slots, _recognize_all(recognizer, crops)):
                result_blocks[block_index].lines[line_index].text = text

        return PageResult(
            source=source,
            width=detection.width,
            height=detection.height,
            blocks=result_blocks,
        )

    def extract_file(self, path: str | Path) -> PageResult:
        path = Path(path)
        image = imread(path)
        return self.extract_page(image, source=str(path))
