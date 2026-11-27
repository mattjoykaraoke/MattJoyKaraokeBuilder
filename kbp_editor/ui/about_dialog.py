from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_AUTHOR, APP_EMAIL, APP_GITHUB_URL, APP_NAME, __version__
from .common import repo_root


def _build_placeholder_logo(size: int = 84) -> QPixmap:
    """Load assets/logo.png if present, otherwise render a clean vector placeholder badge."""
    logo_path = repo_root() / "assets" / "logo.png"
    if logo_path.is_file():
        pm = QPixmap(str(logo_path))
        if not pm.isNull():
            return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(37, 99, 235))
    grad.setColorAt(1.0, QColor(16, 185, 129))
    p.setBrush(grad)
    p.setPen(QPen(QColor(147, 197, 253, 180), 2))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 16, 16)

    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
    p.drawText(QRectF(0, 8, size, size * 0.58), Qt.AlignmentFlag.AlignCenter, "KBP")

    p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
    p.setPen(QColor(226, 232, 240))
    p.drawText(QRectF(0, size * 0.58, size, size * 0.30), Qt.AlignmentFlag.AlignCenter, "EDITOR")
    p.end()
    return pm


class _LicensesDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Licenses & Attribution")
        self.resize(680, 500)

        root = QVBoxLayout(self)
        txt = QPlainTextEdit(self)
        txt.setReadOnly(True)
        txt.setFont(QFont("Consolas", 9))

        parts = []
        for fname in ("LICENSE", "THIRD_PARTY_LICENSES.md"):
            fp = repo_root() / fname
            if fp.is_file():
                parts.append(fp.read_text(encoding="utf-8", errors="replace"))
        txt.setPlainText(("\n\n" + ("=" * 72) + "\n\n").join(parts) if parts else "License files not found.")
        root.addWidget(txt, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)


class AboutDialog(QDialog):
    """About dialog displaying app logo, version, creator info, GitHub link, and license info."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_check_updates: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_check_updates = on_check_updates
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(16)

        logo_lbl = QLabel(self)
        logo_lbl.setPixmap(_build_placeholder_logo(84))
        logo_lbl.setFixedSize(84, 84)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(logo_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        name_lbl = QLabel(f"<b>{APP_NAME}</b>", self)
        name_lbl.setStyleSheet("font-size: 18px; color: #f8fafc;")
        title_col.addWidget(name_lbl)

        ver_lbl = QLabel(f"Version {__version__}", self)
        ver_lbl.setStyleSheet("font-size: 12px; color: #60a5fa; font-weight: bold;")
        title_col.addWidget(ver_lbl)

        desc_lbl = QLabel(
            "A modern karaoke lyric tap-syncing, syllable wiping, and display-window builder and editor of "
            "Karaoke Builder Studio (.kbp) projects, and pipeline to finished video.",
            self,
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 11px; color: #cbd5e1;")
        title_col.addWidget(desc_lbl)
        top.addLayout(title_col, 1)
        root.addLayout(top)

        info_lbl = QLabel(
            f"<b>Created by:</b> {APP_AUTHOR}<br>"
            f"<b>GitHub:</b> <a href='{APP_GITHUB_URL}' style='color: #60a5fa;'>{APP_GITHUB_URL}</a><br>"
            f"<b>Contact:</b> <a href='mailto:{APP_EMAIL}' style='color: #60a5fa;'>{APP_EMAIL}</a><br>"
            "<b>License:</b> Proprietary — All Rights Reserved © 2026 Matt Joy<br>"
            "<span style='color: #94a3b8;'>Uses LGPL / BSD / MIT open-source libraries (see Attribution)</span>",
            self,
        )
        info_lbl.setOpenExternalLinks(True)
        info_lbl.setTextFormat(Qt.TextFormat.RichText)
        info_lbl.setStyleSheet(
            "background-color: #1b1f27; border: 1px solid #323846; border-radius: 6px; "
            "padding: 10px 12px; font-size: 11px; color: #e2e8f0; line-height: 1.5;"
        )
        root.addWidget(info_lbl)

        btns = QHBoxLayout()
        btn_lic = QPushButton("View Licenses && Attribution…", self)
        btn_lic.clicked.connect(self._open_licenses)
        btns.addWidget(btn_lic)
        if self._on_check_updates is not None:
            btn_upd = QPushButton("Check for Updates…", self)
            btn_upd.clicked.connect(self._trigger_check_updates)
            btns.addWidget(btn_upd)
        btns.addStretch()
        btn_ok = QPushButton("Close", self)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btns.addWidget(btn_ok)
        root.addLayout(btns)

    def _open_licenses(self) -> None:
        dlg = _LicensesDialog(self)
        dlg.exec()

    def _trigger_check_updates(self) -> None:
        cb = self._on_check_updates
        self.accept()
        if cb is not None:
            cb()
