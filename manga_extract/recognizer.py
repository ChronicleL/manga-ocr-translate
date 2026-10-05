"""Japanese OCR for detected text lines.

Wraps ``manga-ocr``, which is a VisionEncoderDecoder model specialised for
Japanese manga text (both vertical and horizontal).
"""

from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np
from PIL import Image

DEFAULT_MODEL = "kha-white/manga-ocr-base"


class MangaOcrRecognizer:
    """Thin wrapper around :class:`manga_ocr.MangaOcr`."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        force_cpu: bool = False,
        hf_endpoint: Optional[str] = None,
    ) -> None:
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint

        # Imported lazily so that detection-only usage does not require torch.
        from manga_ocr import MangaOcr

        self._ocr = MangaOcr(pretrained_model_name_or_path=model_name, force_cpu=force_cpu)

    def recognize(self, image_bgr: np.ndarray) -> str:
        """Recognize the Japanese text in a BGR crop."""
        if image_bgr is None or image_bgr.size == 0:
            return ""
        if image_bgr.ndim == 2:
            rgb = image_bgr
        else:
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        return self._ocr(Image.fromarray(rgb))

    __call__ = recognize
