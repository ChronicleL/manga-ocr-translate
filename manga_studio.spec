# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Manga Studio (the PySide6 workbench).

Build:
    .venv\\Scripts\\pyinstaller.exe --noconfirm --clean manga_studio.spec
Output:
    dist/MangaStudio/MangaStudio.exe

Check the build (no window needed):
    set QT_QPA_PLATFORM=offscreen
    dist\\MangaStudio\\MangaStudio.exe --selftest result.txt     -> result.txt starts with "OK"

``packaging/build_release.ps1`` does all of this and zips the result.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas = [
    ("models/comic-text-detector.onnx", "models"),
    ("README.md", "."),
    ("CHANGELOG.md", "."),
]
binaries = []
hiddenimports = []

# Packages that carry non-Python payloads PyInstaller's default hooks miss:
#   manga_ocr         assets/example.jpg
#   unidic_lite       its dictionary (pulled in by fugashi)
#   qfluentwidgets    icons, qss and translations
#   qframelesswindow  its Windows helpers
# PySide6's DLLs are handled by PyInstaller's built-in hook; its plugins are added below.
for package in ("manga_ocr", "unidic_lite", "fugashi", "qfluentwidgets", "qframelesswindow"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Qt plugins (platforms/qwindows.dll, imageformats, styles, ...).
#
# PyInstaller's PySide6 hook finds them by importing PySide6 in a child process. On an
# Anaconda-based environment that import fails (conda's icuuc.dll shadows the system one, see
# manga_extract/studio/bootstrap.py), the hook silently collects nothing, and the packaged
# program cannot create any window. So the plugin folders are added here directly, without
# importing PySide6. The runtime hook expects them at PySide6/plugins.
_pyside = Path(list(importlib.util.find_spec("PySide6").submodule_search_locations)[0])
for _folder in ("platforms", "styles", "imageformats", "iconengines", "generic",
                "platforminputcontexts", "networkinformation", "tls"):
    _source = _pyside / "plugins" / _folder
    if _source.is_dir():
        datas.append((str(_source), f"PySide6/plugins/{_folder}"))

analysis = Analysis(
    ["run_studio.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Not used by the studio. tkinter / customtkinter belong to the older floating tool.
    excludes=["matplotlib", "pytest", "IPython", "notebook", "PyQt5", "PyQt6",
              "tkinter", "customtkinter"],
    noarchive=False,
)

# conda's ICU copies get dragged in as a dependency of Qt6Core.dll when building from Anaconda.
# Their exports carry a version suffix that Qt does not use, so they must not ship: the system's
# own icuuc.dll (preloaded by studio/bootstrap.py) is the right one.
analysis.binaries = [
    entry for entry in analysis.binaries
    if not (Path(entry[0]).name.lower().startswith("icu") and entry[0].lower().endswith(".dll"))
]

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MangaStudio",
    debug=False,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="MangaStudio",
)
