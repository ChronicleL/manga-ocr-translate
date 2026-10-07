# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the floating manga text extractor (GUI).

Build:
    .venv\\Scripts\\pyinstaller.exe --noconfirm manga_ocr_translate.spec
Output:
    dist/MangaOcrTranslate/MangaOcrTranslate.exe
"""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("models/comic-text-detector.onnx", "models"),
    ("README.md", "."),
]
binaries = []
hiddenimports = []

# These carry non-Python payloads that PyInstaller's default hooks miss:
# manga_ocr ships assets/example.jpg, unidic_lite ships its dictionary.
for package in ("manga_ocr", "unidic_lite", "fugashi"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    ["run_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pytest", "IPython", "notebook", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MangaOcrTranslate",
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
    name="MangaOcrTranslate",
)
