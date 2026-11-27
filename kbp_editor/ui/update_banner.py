from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..updater import ReleaseInfo, can_run_windows_installer


class ReleaseNotesDialog(QDialog):
    """Dialog showing release notes for an available update with install / browser actions."""

    install_requested = Signal(object)  # ReleaseInfo
    open_release_page_requested = Signal(object)  # ReleaseInfo

    def __init__(self, release: ReleaseInfo, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._release = release
        self.setWindowTitle(f"What's New — {APP_NAME} v{release.version}")
        self.resize(560, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        hdr = QLabel(
            f"<b>{APP_NAME} v{release.version}</b> is available "
            f"<span style='color: #94a3b8;'>(you have v{__version__})</span>",
            self,
        )
        hdr.setStyleSheet("font-size: 14px; color: #f8fafc;")
        root.addWidget(hdr)

        notes_box = QPlainTextEdit(self)
        notes_box.setReadOnly(True)
        notes_box.setFont(QFont("Segoe UI", 10))
        notes_box.setPlainText(release.notes or "No release notes were provided for this release.")
        notes_box.setStyleSheet(
            "QPlainTextEdit { background-color: #161920; color: #e2e8f0; "
            "border: 1px solid #323846; border-radius: 6px; padding: 8px; }"
        )
        root.addWidget(notes_box, 1)

        btn_row = QHBoxLayout()
        btn_page = QPushButton("Open GitHub Release Page", self)
        btn_page.clicked.connect(self._on_open_page)
        btn_row.addWidget(btn_page)

        btn_row.addStretch()

        if release.installer_url and can_run_windows_installer():
            size_mb = f" ({release.installer_size / (1024 * 1024):.1f} MB)" if release.installer_size > 0 else ""
            btn_install = QPushButton(f"Download && Install{size_mb}", self)
            btn_install.setDefault(True)
            btn_install.setStyleSheet(
                "QPushButton { background-color: #2563eb; color: #ffffff; font-weight: bold; "
                "padding: 5px 14px; border-radius: 4px; border: 1px solid #3b82f6; }"
                "QPushButton:hover { background-color: #1d4ed8; }"
            )
            btn_install.clicked.connect(self._on_install)
            btn_row.addWidget(btn_install)

        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    def _on_install(self) -> None:
        self.install_requested.emit(self._release)
        self.accept()

    def _on_open_page(self) -> None:
        self.open_release_page_requested.emit(self._release)


class UpdateBanner(QFrame):
    """Slim, dismissible top notification bar shown when a newer GitHub release is available."""

    install_requested = Signal(object)  # ReleaseInfo
    open_release_page_requested = Signal(object)  # ReleaseInfo
    skip_version_requested = Signal(str)  # version string

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._release: Optional[ReleaseInfo] = None
        self.setObjectName("updateBanner")
        self.setStyleSheet(
            "QFrame#updateBanner { background-color: #172554; border-bottom: 1px solid #2563eb; }"
            "QLabel { color: #eff6ff; font-size: 12px; }"
            "QPushButton { padding: 3px 10px; font-size: 11px; border-radius: 4px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 10, 6)
        layout.setSpacing(8)

        self._msg_lbl = QLabel("", self)
        self._msg_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._msg_lbl, 1)

        self._primary_btn = QPushButton("Download && Install", self)
        self._primary_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._primary_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; font-weight: bold; "
            "border: 1px solid #60a5fa; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        self._primary_btn.clicked.connect(self._on_primary_clicked)
        layout.addWidget(self._primary_btn)

        self._notes_btn = QPushButton("What's New", self)
        self._notes_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._notes_btn.setStyleSheet(
            "QPushButton { background-color: #1e3a8a; color: #dbeafe; border: 1px solid #3b82f6; }"
            "QPushButton:hover { background-color: #1e40af; }"
        )
        self._notes_btn.clicked.connect(self._on_notes_clicked)
        layout.addWidget(self._notes_btn)

        self._skip_btn = QPushButton("Skip Version", self)
        self._skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #bfdbfe; border: 1px solid #3b82f6; }"
            "QPushButton:hover { background-color: #1e3a8a; color: #ffffff; }"
        )
        self._skip_btn.clicked.connect(self._on_skip_clicked)
        layout.addWidget(self._skip_btn)

        self._close_btn = QPushButton("✕", self)
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_btn.setToolTip("Dismiss for now")
        self._close_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #bfdbfe; border: none; font-weight: bold; }"
            "QPushButton:hover { background-color: #1e3a8a; color: #ffffff; }"
        )
        self._close_btn.clicked.connect(self.hide)
        layout.addWidget(self._close_btn)

        self.hide()

    def show_release(self, release: ReleaseInfo) -> None:
        self._release = release
        self._msg_lbl.setText(
            f"<b>New release available:</b> {APP_NAME} <b>v{release.version}</b> "
            f"<span style='color: #bfdbfe;'>(current: v{__version__})</span>"
        )
        if release.installer_url and can_run_windows_installer():
            self._primary_btn.setText("Download && Install")
            self._primary_btn.setToolTip(f"Download and run {release.installer_name or 'installer'}")
        else:
            self._primary_btn.setText("Open Release Page")
            self._primary_btn.setToolTip(release.html_url)
        self.show()

    def _on_primary_clicked(self) -> None:
        if self._release is None:
            return
        if self._release.installer_url and can_run_windows_installer():
            self.install_requested.emit(self._release)
        else:
            self.open_release_page_requested.emit(self._release)

    def _on_notes_clicked(self) -> None:
        if self._release is None:
            return
        dlg = ReleaseNotesDialog(self._release, self.window())
        dlg.install_requested.connect(self.install_requested.emit)
        dlg.open_release_page_requested.connect(self.open_release_page_requested.emit)
        dlg.exec()

    def _on_skip_clicked(self) -> None:
        if self._release is not None:
            self.skip_version_requested.emit(self._release.version)
        self.hide()
