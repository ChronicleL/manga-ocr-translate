"""Entry point of Manga Studio: splash first, then the main window."""

from __future__ import annotations

import os
import sys
from typing import List, Optional


def _splash_pixmap(dpr: float):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap

    width, height = 420, 190
    pixmap = QPixmap(int(width * dpr), int(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), 14, 14)
    painter.fillPath(path, QColor("#1e293b"))
    painter.setPen(QColor("#f8fafc"))
    title = QFont("Microsoft YaHei UI", 17)
    title.setBold(True)
    painter.setFont(title)
    painter.drawText(QRectF(0, 56, width, 40), Qt.AlignmentFlag.AlignCenter, "漫画汉化工作台")
    painter.setPen(QColor("#94a3b8"))
    painter.setFont(QFont("Microsoft YaHei UI", 10))
    painter.drawText(QRectF(0, 104, width, 30), Qt.AlignmentFlag.AlignCenter, "正在启动…")
    painter.end()
    return pixmap


def main(argv: Optional[List[str]] = None) -> int:
    # Must run before PySide6 is imported (see bootstrap for why).
    from .bootstrap import prepare_windows_dlls

    prepare_windows_dlls()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QSplashScreen

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("MangaStudio")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    # Window and taskbar icon; the executable carries the same one (manga_studio.spec).
    from .config import APP_ICON_PATH

    if APP_ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    splash = QSplashScreen(_splash_pixmap(app.devicePixelRatio()),
                           Qt.WindowType.WindowStaysOnTopHint)
    splash.show()
    app.processEvents()

    # qfluentwidgets is the slowest import, so it stays behind the splash.
    from .config import StudioConfig
    from .main_window import MainWindow
    from .selftest import SelfTest, parse_selftest

    arguments = list(argv if argv is not None else sys.argv)
    selftest_path = parse_selftest(arguments)

    config = StudioConfig.load()
    window = MainWindow(config)
    window.show()
    splash.finish(window)
    if selftest_path:
        # A check run must leave no trace in the user's settings.
        config.save = lambda *args, **kwargs: None
        SelfTest(window, selftest_path)
    return app.exec()


def run(argv: Optional[List[str]] = None) -> None:
    """What the launch scripts call: run the application, then leave at once.

    By the time ``exec()`` returns everything is saved (the window's ``closeEvent`` did it).
    Letting the interpreter and the native libraries tear themselves down only adds risk:
    torch, onnxruntime (DirectML) and Qt unloading in an arbitrary order crashed the process
    with an access violation in roughly one run in three, after the window was already gone.
    """
    code = main(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(code or 0))
