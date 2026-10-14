"""Small constants shared by the studio widgets."""

from __future__ import annotations

from .project import (
    STATUS_PENDING,
    STATUS_RECOGNIZED,
    STATUS_REVIEWED,
    STATUS_TRANSLATED,
)

# Transient states while a page waits in the recognition queue / is being read.
STATUS_QUEUED = "queued"
STATUS_BUSY = "busy"

STATUS_LABELS = {
    STATUS_PENDING: "未识别",
    STATUS_QUEUED: "排队中",
    STATUS_BUSY: "识别中…",
    STATUS_RECOGNIZED: "已识别",
    STATUS_TRANSLATED: "已翻译",
    STATUS_REVIEWED: "已校对",
}

STATUS_COLORS = {
    STATUS_PENDING: "#8a8a8a",
    STATUS_QUEUED: "#6b7a90",
    STATUS_BUSY: "#f7630c",
    STATUS_RECOGNIZED: "#0078d4",
    STATUS_TRANSLATED: "#8764b8",
    STATUS_REVIEWED: "#107c10",
}

# Window background of the Fluent look (light / dark).
BG_LIGHT = "#f3f3f3"
BG_DARK = "#202020"

DEFAULT_ACCENT = "#0078d4"


def system_accent(default: str = DEFAULT_ACCENT) -> str:
    """The user's Windows accent colour as ``#rrggbb`` (``default`` elsewhere)."""
    import sys

    if sys.platform != "win32":
        return default
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as key:
            value, _kind = winreg.QueryValueEx(key, "AccentColorMenu")
    except OSError:
        return default
    # Stored as 0xAABBGGRR.
    red, green, blue = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return f"#{red:02x}{green:02x}{blue:02x}"
