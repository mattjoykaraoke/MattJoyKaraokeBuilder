from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

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
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    initial_file = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(initial_file=initial_file)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
