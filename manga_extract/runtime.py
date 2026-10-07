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


@dataclass
class EngineConfig:
    """How the detector and the OCR recognizer should run."""

    device: str = "auto"   # auto | cpu | cuda | dml | openvino
    threads: int = 0       # 0 = let each backend pick its own default
    ocr_batch: int = 1     # text lines per forward pass

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
        return EngineConfig(device=device, threads=threads, ocr_batch=batch)

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
