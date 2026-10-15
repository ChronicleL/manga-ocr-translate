"""Japanese OCR for detected text lines.

Wraps ``manga-ocr``, which is a VisionEncoderDecoder model specialised for
Japanese manga text (both vertical and horizontal).

Besides plain one-line-at-a-time recognition, this wrapper can feed several
crops to the model in a single forward pass (:meth:`MangaOcrRecognizer.
recognize_many`). Batched decoding keeps the CPU busy instead of paying the
per-call overhead once per text line, which roughly halves the wall time of a
full page on a multi-core CPU.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

import cv2
import numpy as np
from PIL import Image

DEFAULT_MODEL = "kha-white/manga-ocr-base"
DEFAULT_ENG_MODEL = "microsoft/trocr-base-printed"
MAX_NEW_TOKENS = 300


class MangaOcrRecognizer:
    """Thin wrapper around :class:`manga_ocr.MangaOcr`."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        force_cpu: bool = False,
        hf_endpoint: Optional[str] = None,
        device: str = "auto",
        threads: int = 0,
        batch_size: int = 1,
    ) -> None:
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint

        # Imported lazily so that detection-only usage does not require torch.
        import torch
        from manga_ocr import MangaOcr

        if threads and threads > 0:
            torch.set_num_threads(threads)

        if device == "cpu":
            force_cpu = True

        self.batch_size = max(1, int(batch_size))
        self._torch = torch
        self._ocr = MangaOcr(pretrained_model_name_or_path=model_name, force_cpu=force_cpu)
        self.device = str(self._ocr.model.device)
        self.threads = torch.get_num_threads()

    # -- pre-processing ------------------------------------------------------
    @staticmethod
    def _to_pil(image_bgr: np.ndarray) -> Image.Image:
        """Convert a BGR crop to the grayscale-RGB image manga-ocr expects."""
        if image_bgr.ndim == 2:
            rgb = image_bgr
        else:
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb).convert("L").convert("RGB")

    def recognize(self, image_bgr: np.ndarray) -> str:
        """Recognize the Japanese text in a BGR crop."""
        if image_bgr is None or image_bgr.size == 0:
            return ""
        return self._ocr(self._to_pil(image_bgr))

    __call__ = recognize

    def recognize_many(self, images: Sequence[np.ndarray],
                       on_progress=None) -> List[str]:
        """Recognize several crops, preserving order.

        With ``batch_size == 1`` this is a plain loop; larger values run the
        underlying model on ``batch_size`` crops per forward pass.

        ``on_progress``, if given, is called as ``on_progress(done, total)``
        after each batch so callers can update a progress indicator.
        """
        results = [""] * len(images)
        total = len(images)
        if self.batch_size <= 1:
            for index, image in enumerate(images):
                results[index] = self.recognize(image)
                if on_progress:
                    on_progress(index + 1, total)
            return results

        pending = [
            (index, self._to_pil(image))
            for index, image in enumerate(images)
            if image is not None and image.size
        ]
        done = 0
        for start in range(0, len(pending), self.batch_size):
            chunk = pending[start:start + self.batch_size]
            for (index, _), text in zip(chunk, self._decode([pil for _, pil in chunk])):
                results[index] = text
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
        return results

    def _decode(self, images: "List[Image.Image]") -> List[str]:
        """Run the VisionEncoderDecoder over a batch of PIL crops."""
        ocr = self._ocr
        try:
            from manga_ocr.ocr import post_process
        except ImportError:  # pragma: no cover - older manga-ocr layout
            return [self._ocr(image) for image in images]

        pixel_values = ocr.processor(images, return_tensors="pt").pixel_values
        pixel_values = pixel_values.to(ocr.model.device)
        with self._torch.inference_mode():
            # manga-ocr's VisionEncoderDecoder subclass is not recognised by
            # transformers' generate() typing, but this is how it is invoked.
            generated = ocr.model.generate(  # type: ignore[attr-defined]
                pixel_values, max_length=MAX_NEW_TOKENS
            )
        decoded = ocr.tokenizer.batch_decode(generated, skip_special_tokens=True)
        return [post_process(text) for text in decoded]


class TrOCRRecognizer:
    """Generic VisionEncoderDecoder recognizer for non-Japanese languages.

    Uses ``transformers`` directly (no manga-ocr post-processing) so it can
    handle any compatible model — e.g. English ``microsoft/trocr-base-printed``
    or a user-supplied Korean model.

    Provides the same ``recognize`` / ``recognize_many`` interface as
    :class:`MangaOcrRecognizer` so it is a drop-in alternative in the pipeline.
    """

    def __init__(
        self,
        model_name: str,
        device: str = "auto",
        threads: int = 0,
        batch_size: int = 1,
        hf_endpoint: Optional[str] = None,
    ) -> None:
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint

        import torch
        from transformers import AutoProcessor, VisionEncoderDecoderModel

        if threads and threads > 0:
            torch.set_num_threads(threads)

        # TrOCR runs on CUDA or CPU; DML is not officially supported.
        if device == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        elif device == "cpu":
            dev = "cpu"
        else:
            dev = "cpu"          # unknown/DML → CPU fallback

        self._processor = AutoProcessor.from_pretrained(model_name)
        self._model = VisionEncoderDecoderModel.from_pretrained(model_name).to(dev)
        self._model.eval()
        self._torch = torch
        self.device = dev
        self.batch_size = max(1, int(batch_size))
        self.threads = torch.get_num_threads()

    # -- pre-processing (identical to MangaOcrRecognizer) -------------------
    @staticmethod
    def _to_pil(image_bgr: np.ndarray) -> Image.Image:
        if image_bgr.ndim == 2:
            rgb = image_bgr
        else:
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb).convert("L").convert("RGB")

    def recognize(self, image_bgr: np.ndarray) -> str:
        if image_bgr is None or image_bgr.size == 0:
            return ""
        pil = self._to_pil(image_bgr)
        pixel_values = (self._processor(pil, return_tensors="pt")
                        .pixel_values.to(self._model.device))
        with self._torch.inference_mode():
            generated = self._model.generate(pixel_values, max_new_tokens=MAX_NEW_TOKENS)
        return self._processor.batch_decode(generated, skip_special_tokens=True)[0]

    __call__ = recognize

    def recognize_many(self, images: Sequence[np.ndarray],
                       on_progress=None) -> List[str]:
        results = [""] * len(images)
        total = len(images)
        if self.batch_size <= 1:
            for index, image in enumerate(images):
                results[index] = self.recognize(image)
                if on_progress:
                    on_progress(index + 1, total)
            return results

        pending = [
            (index, self._to_pil(image))
            for index, image in enumerate(images)
            if image is not None and image.size
        ]
        done = 0
        for start in range(0, len(pending), self.batch_size):
            chunk = pending[start:start + self.batch_size]
            pils = [pil for _, pil in chunk]
            pixel_values = (self._processor(pils, return_tensors="pt", padding=True)
                            .pixel_values.to(self._model.device))
            with self._torch.inference_mode():
                generated = self._model.generate(pixel_values, max_new_tokens=MAX_NEW_TOKENS)
            texts = self._processor.batch_decode(generated, skip_special_tokens=True)
            for (index, _), text in zip(chunk, texts):
                results[index] = text
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
        return results
