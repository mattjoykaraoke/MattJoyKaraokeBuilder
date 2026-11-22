from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from kbp_editor import APP_NAME
from kbp_editor.ui.common import load_app_icon
from kbp_editor.ui.main_window import MainWindow


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(30, 33, 40))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(232, 236, 244))
    pal.setColor(QPalette.ColorRole.Base, QColor(22, 25, 30))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(34, 38, 46))
    pal.setColor(QPalette.ColorRole.Text, QColor(232, 236, 244))
    pal.setColor(QPalette.ColorRole.Button, QColor(45, 50, 60))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(232, 236, 244))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(59, 130, 246))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)


def main() -> int:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MattJoy.MattJoyKaraokeBuilder")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    apply_dark_theme(app)
    initial_file = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(initial_file=initial_file)
    if not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
