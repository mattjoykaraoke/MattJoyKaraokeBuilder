from __future__ import annotations

import argparse
import ctypes
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from kbp_editor import APP_NAME
from kbp_editor.ui.common import load_app_icon
from kbp_editor.ui.main_window import MainWindow


def parse_cli_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("file", nargs="?", default=None)
    parser.add_argument("--audio", "--main-audio", dest="audio", default=None)
    parser.add_argument("--vocal", "--vocal-stem", dest="vocal", default=None)
    args, _unknown = parser.parse_known_args(argv)
    return args


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
    cli_args = parse_cli_args(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    apply_dark_theme(app)
    win = MainWindow(
        initial_file=cli_args.file,
        initial_audio=cli_args.audio,
        initial_vocal=cli_args.vocal,
    )
    if not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
