from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ass_export import (
    OVERFLOW_CHOICES,
    RESOLUTION_PRESETS,
    AssExportOptions,
    convert_project_to_ass,
    suggest_ass_path,
)
from ..model import KbpProject


class FinishingView(QWidget):
    """
    'Finishing' tab for post-sync finalization and export tools,
    including standalone .kbp -> .ass conversion via kbputils.
    """

    status_message = Signal(str, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project: Optional[KbpProject] = None
        self._last_ass_path: Optional[Path] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(self)
        card.setMinimumWidth(520)
        card.setMaximumWidth(640)
        card.setStyleSheet(
            "QFrame#finishingCard { background-color: #20242c; border: 1px solid #353b47; border-radius: 8px; }"
            "QFrame#sectionBox { background-color: #191d24; border: 1px solid #2e3440; border-radius: 6px; }"
            "QLabel { border: none; }"
        )
        card.setObjectName("finishingCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 24, 28, 24)
        cl.setSpacing(14)

        title = QLabel("Finishing", card)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #f1f5f9;")
        cl.addWidget(title)

        subtitle = QLabel("Export your finished sync and prepare subtitle files for video rendering.", card)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px; color: #94a3b8;")
        cl.addWidget(subtitle)

        self._project_lbl = QLabel("No project loaded.", card)
        self._project_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._project_lbl.setWordWrap(True)
        self._project_lbl.setStyleSheet("font-size: 12px; color: #cbd5e1;")
        cl.addWidget(self._project_lbl)

        self._source_lbl = QLabel("", card)
        self._source_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source_lbl.setWordWrap(True)
        self._source_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        self._source_lbl.hide()
        cl.addWidget(self._source_lbl)

        # --- Convert .kbp to .ass Section ---
        ass_box = QFrame(card)
        ass_box.setObjectName("sectionBox")
        al = QVBoxLayout(ass_box)
        al.setContentsMargins(18, 16, 18, 16)
        al.setSpacing(12)

        sec_title = QLabel(
            "Convert .kbp to .ass "
            "<span style='font-size: 12px; font-weight: normal; color: #94a3b8;'>"
            "(leave default if not sure)</span>",
            ass_box,
        )
        sec_title.setTextFormat(Qt.TextFormat.RichText)
        sec_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f1f5f9;")
        al.addWidget(sec_title)

        sec_desc = QLabel(
            "Generates an Advanced SubStation Alpha (.ass) karaoke subtitle file directly "
            "from the active project using kbputils.",
            ass_box,
        )
        sec_desc.setWordWrap(True)
        sec_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        al.addWidget(sec_desc)

        # Compact export options grid
        opts_grid = QGridLayout()
        opts_grid.setHorizontalSpacing(12)
        opts_grid.setVerticalSpacing(8)

        lbl_style = "font-size: 12px; color: #cbd5e1;"

        res_lbl = QLabel("Resolution:", ass_box)
        res_lbl.setStyleSheet(lbl_style)
        self._res_combo = QComboBox(ass_box)
        for label, w, h in RESOLUTION_PRESETS:
            self._res_combo.addItem(label, (w, h))
        opts_grid.addWidget(res_lbl, 0, 0)
        opts_grid.addWidget(self._res_combo, 0, 1)

        ov_lbl = QLabel("Line Overflow:", ass_box)
        ov_lbl.setStyleSheet(lbl_style)
        self._overflow_combo = QComboBox(ass_box)
        for key, label in OVERFLOW_CHOICES:
            self._overflow_combo.addItem(label, key)
        opts_grid.addWidget(ov_lbl, 0, 2)
        opts_grid.addWidget(self._overflow_combo, 0, 3)

        fi_lbl = QLabel("Fade In (ms):", ass_box)
        fi_lbl.setStyleSheet(lbl_style)
        self._fade_in_spin = QSpinBox(ass_box)
        self._fade_in_spin.setRange(0, 5000)
        self._fade_in_spin.setSingleStep(50)
        self._fade_in_spin.setValue(0)
        opts_grid.addWidget(fi_lbl, 1, 0)
        opts_grid.addWidget(self._fade_in_spin, 1, 1)

        fo_lbl = QLabel("Fade Out (ms):", ass_box)
        fo_lbl.setStyleSheet(lbl_style)
        self._fade_out_spin = QSpinBox(ass_box)
        self._fade_out_spin.setRange(0, 5000)
        self._fade_out_spin.setSingleStep(50)
        self._fade_out_spin.setValue(0)
        opts_grid.addWidget(fo_lbl, 1, 2)
        opts_grid.addWidget(self._fade_out_spin, 1, 3)

        al.addLayout(opts_grid)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._convert_btn = QPushButton("Convert .kbp to .ass", ass_box)
        self._convert_btn.setMinimumHeight(36)
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; font-size: 13px; "
            "font-weight: bold; border: none; border-radius: 5px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
            "QPushButton:pressed { background-color: #1e40af; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self._convert_btn.setEnabled(False)
        self._convert_btn.setToolTip("Load or sync a KBP project first")
        self._convert_btn.clicked.connect(self._on_convert_to_ass)
        btn_row.addWidget(self._convert_btn, 1)

        self._open_folder_btn = QPushButton("Open Output Folder", ass_box)
        self._open_folder_btn.setMinimumHeight(36)
        self._open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_folder_btn.setVisible(False)
        self._open_folder_btn.clicked.connect(self._on_open_output_folder)
        btn_row.addWidget(self._open_folder_btn)

        al.addLayout(btn_row)

        self._status_lbl = QLabel("", ass_box)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #94a3b8; padding: 8px 10px; "
            "border-radius: 5px; background-color: #1e293b; border: 1px solid #334155; }"
        )
        self._status_lbl.hide()
        al.addWidget(self._status_lbl)

        cl.addWidget(ass_box)
        root.addWidget(card)

    def current_options(self) -> AssExportOptions:
        res_data = self._res_combo.currentData()
        target_x, target_y = res_data if isinstance(res_data, tuple) else (1920, 1080)
        overflow_key = str(self._overflow_combo.currentData() or "no_wrap")
        return AssExportOptions(
            target_x=int(target_x),
            target_y=int(target_y),
            fade_in=int(self._fade_in_spin.value()),
            fade_out=int(self._fade_out_spin.value()),
            overflow=overflow_key,
        )

    def set_project(self, project: Optional[KbpProject]) -> None:
        is_new_project = project is not self.project
        self.project = project
        if is_new_project:
            self._last_ass_path = None
            self._open_folder_btn.setVisible(False)
            self._status_lbl.hide()

        if project is None:
            self._project_lbl.setText("No project loaded.")
            self._source_lbl.hide()
            self._convert_btn.setEnabled(False)
            self._convert_btn.setToolTip("Load or sync a KBP project first")
            return

        name = (
            f"{project.header.artist} - {project.header.title}"
            if (project.header.artist and project.header.title)
            else (project.header.title or (project.source_path.name if project.source_path else "Untitled Project"))
        )
        total_syls = sum(len(ln.syllables) for p in project.pages for ln in p.lines)
        self._project_lbl.setText(f"Active project: {name}  •  {len(project.pages)} pages  •  {total_syls} syllables")

        if project.source_path is not None:
            auto_ass = project.source_path.with_suffix(".ass")
            self._source_lbl.setText(f"Auto output: {auto_ass}")
            self._source_lbl.show()
            self._convert_btn.setToolTip(f"Convert directly to {auto_ass}")
        else:
            self._source_lbl.setText("Unsaved in-memory project (save as .kbp first for automatic same-folder output)")
            self._source_lbl.show()
            self._convert_btn.setToolTip("Export the active synced project as an .ass subtitle file")

        self._convert_btn.setEnabled(True)

    def _on_convert_to_ass(self) -> None:
        if self.project is None:
            return

        if self.project.source_path is not None:
            out_path = self.project.source_path.with_suffix(".ass")
        else:
            default_path = suggest_ass_path(self.project)
            out_str, _ = QFileDialog.getSaveFileName(
                self,
                "Convert .kbp to .ass",
                str(default_path),
                "SubStation Alpha Subtitles (*.ass)",
            )
            if not out_str:
                return
            out_path = Path(out_str)
            if out_path.suffix.lower() != ".ass":
                out_path = out_path.with_suffix(".ass")

        try:
            saved = convert_project_to_ass(self.project, out_path, self.current_options())
        except Exception as exc:
            self._status_lbl.setStyleSheet(
                "QLabel { font-size: 12px; color: #fecaca; padding: 8px 10px; "
                "border-radius: 5px; background-color: #450a0a; border: 1px solid #991b1b; }"
            )
            self._status_lbl.setText(f"Conversion failed: {exc}")
            self._status_lbl.show()
            self.status_message.emit(f"ASS conversion failed: {exc}", 6000)
            return

        self._last_ass_path = saved
        self._open_folder_btn.setVisible(True)
        self._status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #bbf7d0; padding: 8px 10px; "
            "border-radius: 5px; background-color: #052e16; border: 1px solid #166534; }"
        )
        self._status_lbl.setText(f"✓ Successfully converted to {saved.name}\n{saved}")
        self._status_lbl.show()
        self.status_message.emit(f"Converted to {saved.name} ({saved.parent})", 6000)

    def _on_open_output_folder(self) -> None:
        if self._last_ass_path is None:
            return
        if sys.platform == "win32" and self._last_ass_path.exists():
            subprocess.Popen(["explorer", f"/select,{self._last_ass_path}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_ass_path.parent)))
