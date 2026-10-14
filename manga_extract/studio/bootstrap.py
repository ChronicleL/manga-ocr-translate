"""Process start-up fixes that must run before PySide6 is imported.

Why this exists
---------------
On a Python that comes from Anaconda, ``Library\\bin`` is on ``PATH`` and holds
conda's own ``icuuc.dll`` (ICU 73). Its exports carry a version suffix, whereas
Qt 6.12 imports the *unsuffixed* names that Windows' built-in
``System32\\icuuc.dll`` provides. The same file name wins the lookup, so
``import PySide6.QtCore`` fails with "DLL load failed ... 找不到指定的程序".

Loading the system copy by absolute path first makes every later by-name
lookup reuse it. This touches neither Anaconda nor the system.

Import order also matters: PySide6 must be imported *before* torch /
onnxruntime / cv2 (the reverse order breaks ``win32api`` for the Fluent
widgets), which is why the engine is loaded lazily in a worker thread.
"""

from __future__ import annotations

import ctypes
import os
import sys


def prepare_windows_dlls() -> bool:
    """Preload the system ICU. Returns True when something was loaded."""
    if sys.platform != "win32":
        return False
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    icu = os.path.join(system_root, "System32", "icuuc.dll")
    if not os.path.isfile(icu):
        return False
    try:
        ctypes.WinDLL(icu)
    except OSError:
        return False
    return True
