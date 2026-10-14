"""Launch the floating manga text extractor window.

Double-click ``run_gui.bat`` (or run this file) to start it. The packaged
executable uses this same entry point.

Start-up sequence
-----------------
1. ``tkinter`` is imported and a borderless splash window is shown immediately.
2. All heavy modules (cv2, onnxruntime, PIL, etc.) are imported in a background
   thread while the splash keeps the event loop alive with ``update()``.
3. Once the import thread finishes the splash is dismissed and the same
   :class:`tk.Tk` instance is handed to :func:`main` so there is never a
   second ``Tk()`` constructor call in the same process.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Environment / path setup (must come before any manga_extract import)
# ---------------------------------------------------------------------------

def _prepare_environment() -> None:
    # Prefer the Hugging Face mirror for the one-time OCR model download.
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # A windowed build has no console, so sys.stdout/stderr are None and any
    # library that logs (loguru, transformers) would fail when writing to them.
    if sys.stdout is not None and sys.stderr is not None:
        return
    target = os.devnull
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "manga_extract.log"
        try:
            candidate.touch()
            target = str(candidate)
        except OSError:
            pass
    stream = open(target, "w", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _add_source_root() -> None:
    if getattr(sys, "frozen", False):
        return  # PyInstaller already configured sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))


_prepare_environment()
_add_source_root()


# ---------------------------------------------------------------------------
# Splash window — only tkinter needed, appears before any heavy import
# ---------------------------------------------------------------------------

import customtkinter as ctk    # noqa: E402
import tkinter as tk          # noqa: E402

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

_root = ctk.CTk()
_root.overrideredirect(True)          # no title bar / borders
_root.attributes("-topmost", True)
_root.configure(fg_color="#1e293b")

_SW, _SH = 380, 190
_sx = (_root.winfo_screenwidth()  - _SW) // 2
_sy = (_root.winfo_screenheight() - _SH) // 2
_root.geometry(f"{_SW}x{_SH}+{_sx}+{_sy}")

ctk.CTkLabel(
    _root, text="漫画文字提取", fg_color="transparent", text_color="#f8fafc",
    font=ctk.CTkFont(size=15, weight="bold"),
).pack(pady=(42, 6))
ctk.CTkLabel(
    _root, text="正在初始化，请稍候…", fg_color="transparent", text_color="#94a3b8",
    font=ctk.CTkFont(size=10),
).pack()

_pb = ctk.CTkProgressBar(_root, mode="indeterminate", width=320)
_pb.pack(pady=(18, 0))
_pb.start()
_root.update()


# ---------------------------------------------------------------------------
# Heavy import in a daemon thread; splash spins until done
# ---------------------------------------------------------------------------

_import_done = threading.Event()
_main_fn = None


def _do_import() -> None:
    global _main_fn
    from manga_extract.gui.app import main  # noqa: E402 – intentional late import
    _main_fn = main
    _import_done.set()


threading.Thread(target=_do_import, daemon=True).start()

while not _import_done.is_set():
    try:
        _root.update()
    except tk.TclError:
        break
    time.sleep(0.04)   # ~25 fps, gives the import thread CPU headroom

# Dismiss splash visuals; hand the Tk instance to main() for reuse.
_pb.stop()
_root.overrideredirect(False)
_root.attributes("-topmost", False)
# Destroy every splash widget before the real app builds its UI into this window.
for _w in list(_root.winfo_children()):
    _w.destroy()
_root.configure(fg_color=ctk.ThemeManager.theme["CTk"]["fg_color"])
_root.update()


# ---------------------------------------------------------------------------
# Launch the real app
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(_main_fn(root=_root))  # type: ignore[misc]
