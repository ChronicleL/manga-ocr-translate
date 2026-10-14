"""Small image helpers shared by the clipboard, screenshot and web importers."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


def qimage_to_png(image: QImage) -> bytes:
    """Encode a QImage as PNG bytes."""
    if image.isNull():
        raise ValueError("没有可保存的图像")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("无法把图像编码成 PNG")
    return bytes(buffer.data())
