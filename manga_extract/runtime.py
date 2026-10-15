"""Performance settings shared by the CLI and the GUI.

These control *how much compute* detection and OCR are allowed to use. They are
deliberately separate from the translation settings: translation is about
network APIs, these are about local inference.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .detector import available_devices

DEVICE_LABELS = {
    "auto": "自动（有 GPU 就用）",
    "cpu": "仅 CPU",
    "cuda": "CUDA（NVIDIA 显卡）",
    "dml": "DirectML（Windows 通用）",
    "openvino": "OpenVINO（Intel）",
}

OCR_BATCH_CHOICES = (1, 2, 4, 8, 12, 16)
MAX_OCR_BATCH = 16


PRIMARY_LANGS = ("ja", "ko")     # detector can't output "ko", so ko = treat "ja"/"unknown" as Korean

@dataclass
class EngineConfig:
    """How the detector and the OCR recognizer should run."""

    device: str = "auto"   # auto | cpu | cuda | dml | openvino
    threads: int = 0       # 0 = let each backend pick its own default
    ocr_batch: int = 1     # text lines per forward pass
    # Multilingual OCR
    primary_lang: str = "ja"    # "ja" | "ko"  — language of non-English blocks
    ko_ocr_model: str = ""      # HuggingFace model id for Korean; empty = not enabled
    eng_ocr_enabled: bool = True
    eng_ocr_model: str = "microsoft/trocr-base-printed"  # empty = disabled

    def normalized(self) -> "EngineConfig":
        """Clamp values into a usable range, dropping unavailable devices."""
        device = self.device if self.device in available_devices() else "auto"
        try:
            threads = max(0, int(self.threads))
        except (TypeError, ValueError):
            threads = 0
        try:
            batch = min(MAX_OCR_BATCH, max(1, int(self.ocr_batch)))
        except (TypeError, ValueError):
            batch = 1
        primary_lang = self.primary_lang if self.primary_lang in PRIMARY_LANGS else "ja"
        return EngineConfig(device=device, threads=threads, ocr_batch=batch,
                            primary_lang=primary_lang,
                            ko_ocr_model=str(self.ko_ocr_model or "").strip(),
                            eng_ocr_enabled=bool(self.eng_ocr_enabled),
                            eng_ocr_model=str(self.eng_ocr_model or "").strip())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> "EngineConfig":
        """Build from untrusted JSON, ignoring unknown keys and bad values."""
        if not isinstance(data, dict):
            return cls()
        allowed = set(cls.__dataclass_fields__)
        try:
            return cls(**{key: value for key, value in data.items() if key in allowed}).normalized()
        except TypeError:
            return cls()

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "EngineConfig":
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8"))).normalized()
        except (json.JSONDecodeError, OSError):
            return cls()
