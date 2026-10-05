"""Image reading/writing helpers that are safe for non-ASCII paths on Windows."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")


def imread(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Read an image, supporting non-ASCII paths (cv2.imread cannot)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        raise FileNotFoundError(f"Cannot read image: {path}")
    img = cv2.imdecode(data, flags)
    if img is None:
        raise ValueError(f"Unsupported or corrupt image: {path}")
    return img


def imwrite(path: str | Path, img: np.ndarray) -> None:
    suffix = Path(path).suffix or ".png"
    ok, buf = cv2.imencode(suffix, img)
    if not ok:
        raise ValueError(f"Failed to encode image: {path}")
    buf.tofile(str(path))


def find_images(path: str | Path) -> list[Path]:
    """Return image files for a file or directory path, sorted by name."""
    path = Path(path)
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(
            p for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
    raise FileNotFoundError(f"No such file or directory: {path}")
