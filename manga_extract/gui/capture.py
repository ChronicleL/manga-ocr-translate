"""Full-screen region capture with a frozen-screen selection overlay.

A screenshot of the whole virtual desktop is taken first and then shown in a
borderless window, so what the user selects is exactly what was on screen
(the overlay itself never appears in the capture).
"""

from __future__ import annotations

import ctypes
from typing import Optional, Tuple

import tkinter as tk
from PIL import Image, ImageGrab, ImageTk

# GetSystemMetrics indices for the virtual desktop.
_VIRTUAL_LEFT = 76
_VIRTUAL_TOP = 77
_VIRTUAL_WIDTH = 78
_VIRTUAL_HEIGHT = 79

MIN_SELECTION = 8
_DIM_STIPPLE = "gray50"
_ACCENT = "#2f9bff"


def enable_dpi_awareness() -> None:
    """Make the process DPI aware so screen coordinates match real pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def virtual_screen_geometry() -> Tuple[int, int, int, int]:
    """Return ``(left, top, width, height)`` of the whole virtual desktop."""
    try:
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(_VIRTUAL_LEFT))
        top = int(user32.GetSystemMetrics(_VIRTUAL_TOP))
        width = int(user32.GetSystemMetrics(_VIRTUAL_WIDTH))
        height = int(user32.GetSystemMetrics(_VIRTUAL_HEIGHT))
        if width > 0 and height > 0:
            return left, top, width, height
    except Exception:
        pass
    try:
        return (0, 0, *ImageGrab.grab().size)
    except Exception:
        return 0, 0, 1920, 1080


def grab_screen() -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Capture the whole virtual desktop.

    Returns the RGB screenshot and its ``(left, top, width, height)`` bounds in
    screen coordinates.
    """
    left, top, width, height = virtual_screen_geometry()
    try:
        shot = ImageGrab.grab(all_screens=True)
    except TypeError:  # older Pillow without all_screens
        shot = ImageGrab.grab()
    shot = shot.convert("RGB")
    if shot.size != (width, height):
        width, height = shot.size
    return shot, (left, top, width, height)


def _geometry(width: int, height: int, left: int, top: int) -> str:
    # Tk needs explicit signs, e.g. "800x600-100+0" for negative offsets.
    return f"{width}x{height}{left:+d}{top:+d}"


class _RegionOverlay(tk.Toplevel):
    """Full-desktop overlay the user drags a selection rectangle on."""

    def __init__(self, master: tk.Misc, screenshot: Image.Image, origin: Tuple[int, int, int, int]):
        super().__init__(master)
        self.result: Optional[Tuple[int, int, int, int]] = None

        left, top, width, height = origin
        self._screen_w, self._screen_h = width, height
        self._start: Optional[Tuple[int, int]] = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(_geometry(width, height, left, top))
        self.configure(bg="black")

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, cursor="crosshair",
                                width=width, height=height)
        self.canvas.pack(fill="both", expand=True)

        self._photo = ImageTk.PhotoImage(screenshot)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        # Four dimming rectangles leave the selection area undimmed.
        self._dim_ids = [
            self.canvas.create_rectangle(0, 0, 0, 0, fill="#000000",
                                         stipple=_DIM_STIPPLE, outline="")
            for _ in range(4)
        ]
        self._outline = self.canvas.create_rectangle(0, 0, 0, 0, outline=_ACCENT, width=2)
        self._size_text = self.canvas.create_text(0, 0, anchor="nw", fill="#ffffff",
                                                  font=("Segoe UI", 10), text="")
        self._hint = self.canvas.create_text(
            width // 2, 40, anchor="n", fill="#ffffff", font=("Segoe UI", 13),
            text="拖动鼠标框选要提取的区域　·　Esc / 右键取消",
        )

        self._update_dimming(0, 0, 0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda _e: self._finish(None))
        self.bind("<Button-3>", lambda _e: self._finish(None))

        self.focus_force()
        self.lift()

    # -- interaction ---------------------------------------------------------
    def _clamp(self, x: int, y: int) -> Tuple[int, int]:
        return max(0, min(x, self._screen_w)), max(0, min(y, self._screen_h))

    def _on_press(self, event: tk.Event) -> None:
        self._start = self._clamp(int(event.x), int(event.y))
        self.canvas.itemconfigure(self._hint, text="")
        self._update_dimming(*self._start, *self._start)

    def _on_drag(self, event: tk.Event) -> None:
        if self._start is None:
            return
        x, y = self._clamp(int(event.x), int(event.y))
        self._update_dimming(self._start[0], self._start[1], x, y)

    def _on_release(self, event: tk.Event) -> None:
        if self._start is None:
            self._finish(None)
            return
        x, y = self._clamp(int(event.x), int(event.y))
        box = _normalize(self._start[0], self._start[1], x, y)
        if box[2] - box[0] < MIN_SELECTION or box[3] - box[1] < MIN_SELECTION:
            self._finish(None)
            return
        self._finish(box)

    def _update_dimming(self, x1: int, y1: int, x2: int, y2: int) -> None:
        x1, y1, x2, y2 = _normalize(x1, y1, x2, y2)
        rects = [
            (0, 0, self._screen_w, y1),            # above
            (0, y2, self._screen_w, self._screen_h),  # below
            (0, y1, x1, y2),                       # left
            (x2, y1, self._screen_w, y2),          # right
        ]
        for rect_id, coords in zip(self._dim_ids, rects):
            self.canvas.coords(rect_id, *coords)

        self.canvas.coords(self._outline, x1, y1, x2, y2)
        w, h = x2 - x1, y2 - y1
        if w > 0 and h > 0:
            self.canvas.itemconfigure(self._size_text, text=f"{w} × {h}")
            self.canvas.coords(self._size_text, x1 + 4, max(0, y1 - 20))
        else:
            self.canvas.itemconfigure(self._size_text, text="")

    def _finish(self, result: Optional[Tuple[int, int, int, int]]) -> None:
        self.result = result
        self.destroy()


def _normalize(x1: int, y1: int, x2: int, y2: int) -> Tuple[int, int, int, int]:
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def select_region(master: tk.Misc) -> Optional[Image.Image]:
    """Show the selection overlay and return the cropped screenshot, or None."""
    screenshot, origin = grab_screen()
    overlay = _RegionOverlay(master, screenshot, origin)
    master.wait_window(overlay)
    if overlay.result is None:
        return None
    x1, y1, x2, y2 = overlay.result
    x2 = min(x2, screenshot.width)
    y2 = min(y2, screenshot.height)
    if x2 <= x1 or y2 <= y1:
        return None
    return screenshot.crop((x1, y1, x2, y2))
