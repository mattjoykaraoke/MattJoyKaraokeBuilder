from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QFileSystemWatcher, QSettings, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
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
from ..chromalyric import (
    CHROMALYRIC_DOWNLOAD_URL,
    ChromaLyricCliOptions,
    find_chromalyric_exe,
    launch_chromalyric,
)
from ..model import KbpProject
from ..video_export import (
    FFMPEG_DOWNLOAD_URL,
    check_ass_should_be_transparent,
    find_ffmpeg_exe,
    parse_ass_background_color,
    prepend_title_card,
    render_karaoke_video,
)


class _TitleCardWorker(QThread):
    """Background worker that encodes the short title card and stream-copy concatenates it."""

    progress = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        ffmpeg_exe: Path,
        video_path: Path,
        image_path: Path,
        output_path: Path,
        duration_sec: float,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ffmpeg_exe = ffmpeg_exe
        self._video_path = video_path
        self._image_path = image_path
        self._output_path = output_path
        self._duration_sec = duration_sec

    def run(self) -> None:
        try:
            saved = prepend_title_card(
                self._ffmpeg_exe,
                self._video_path,
                self._image_path,
                self._output_path,
                self._duration_sec,
                progress_cb=self.progress.emit,
            )
            self.succeeded.emit(str(saved))
        except Exception as exc:
            self.failed.emit(str(exc))


class _VideoRenderWorker(QThread):
    """Background worker that runs FFmpeg karaoke video rendering without freezing the UI."""

    progress = Signal(str)
    succeeded = Signal(str)  # final output video path
    failed = Signal(str)

    def __init__(
        self,
        ffmpeg_exe: Path,
        ass_path: Path,
        audio_path: Path,
        output_path: Path,
        bg_color: str,
        transparent: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ffmpeg_exe = ffmpeg_exe
        self._ass_path = ass_path
        self._audio_path = audio_path
        self._output_path = output_path
        self._bg_color = bg_color
        self._transparent = transparent
        self._cancel_event = threading.Event()
        self._process_holder: Dict[str, object] = {}

    def cancel(self) -> None:
        self._cancel_event.set()
        proc = self._process_holder.get("proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            saved = render_karaoke_video(
                self._ffmpeg_exe,
                self._ass_path,
                self._audio_path,
                self._output_path,
                self._bg_color,
                transparent=self._transparent,
                progress_cb=self.progress.emit,
                cancel_event=self._cancel_event,
                process_holder=self._process_holder,
            )
            self.succeeded.emit(str(saved))
        except Exception as exc:
            self.failed.emit(str(exc))


class _ChromaLyricWorker(QThread):
    """Background worker so ChromaLyric CLI styling never freezes the Finishing tab."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        exe_path: Path,
        ass_path: Path,
        options: Optional[ChromaLyricCliOptions] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._exe_path = exe_path
        self._ass_path = ass_path
        self._options = options

    def run(self) -> None:
        try:
            msg = launch_chromalyric(self._exe_path, self._ass_path, self._options)
            self.succeeded.emit(msg)
        except Exception as exc:
            self.failed.emit(str(exc))


class FinishingView(QWidget):
    """
    'Finishing' tab for post-sync finalization and export tools:
    - Standalone .kbp -> .ass conversion via kbputils
    - Direct launch in ChromaLyric (with optional collapsible CLI styling options)
    """

    status_message = Signal(str, int)
    _CL_SETTINGS_KEY = "chromalyric_exe"
    _FFMPEG_SETTINGS_KEY = "ffmpeg_exe"

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        settings: Optional[QSettings] = None,
    ) -> None:
        super().__init__(parent)
        self.project: Optional[KbpProject] = None
        self._last_ass_path: Optional[Path] = None
        self._last_video_path: Optional[Path] = None
        self._last_tc_video_path: Optional[Path] = None
        self._settings = settings or QSettings("MattJoy", "MattJoyKaraokeBuilder")
        self._cl_worker: Optional[_ChromaLyricWorker] = None
        self._video_worker: Optional[_VideoRenderWorker] = None
        self._tc_worker: Optional[_TitleCardWorker] = None
        self._custom_video_out: bool = False
        self._custom_tc_out: bool = False
        self._watched_ass_path: Optional[Path] = None
        self._last_ass_stat: Optional[tuple[str, int, int]] = None

        self._ass_watcher = QFileSystemWatcher(self)
        self._ass_watcher.fileChanged.connect(self._schedule_ass_refresh)
        self._ass_watcher.directoryChanged.connect(self._schedule_ass_refresh)

        self._ass_refresh_timer = QTimer(self)
        self._ass_refresh_timer.setSingleShot(True)
        self._ass_refresh_timer.setInterval(120)
        self._ass_refresh_timer.timeout.connect(self._check_watched_ass_changed)

        self._ass_poll_timer = QTimer(self)
        self._ass_poll_timer.setInterval(1000)
        self._ass_poll_timer.timeout.connect(self._check_watched_ass_changed)
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        outer.addWidget(scroll)

        container = QWidget(scroll)
        root = QVBoxLayout(container)
        root.setContentsMargins(24, 24, 24, 24)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        scroll.setWidget(container)

        card = QFrame(container)
        card.setMinimumWidth(540)
        card.setMaximumWidth(660)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        card.setStyleSheet(
            "QFrame#finishingCard { background-color: #20242c; border: 1px solid #353b47; border-radius: 8px; }"
            "QFrame#sectionBox { background-color: #191d24; border: 1px solid #2e3440; border-radius: 6px; }"
            "QFrame#advBox { background-color: #161a22; border: 1px solid #2e3545; border-radius: 5px; }"
            "QLabel { border: none; }"
            "QRadioButton, QCheckBox { color: #e2e8f0; font-size: 12px; spacing: 8px; min-height: 24px; }"
            "QRadioButton::indicator { width: 15px; height: 15px; border-radius: 8px; "
            "border: 1.5px solid #64748b; background-color: #0f131a; }"
            "QRadioButton::indicator:hover { border-color: #93c5fd; }"
            "QRadioButton::indicator:checked { border: 4px solid #2563eb; background-color: #ffffff; }"
            "QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px; "
            "border: 1.5px solid #64748b; background-color: #0f131a; }"
            "QCheckBox::indicator:hover { border-color: #93c5fd; }"
            "QCheckBox::indicator:checked { border-color: #2563eb; background-color: #2563eb; }"
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

        # --- Section 1: Convert .kbp to .ass ---
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

        # --- Section 2: Open in ChromaLyric ---
        cl_box = QFrame(card)
        cl_box.setObjectName("sectionBox")
        chl = QVBoxLayout(cl_box)
        chl.setContentsMargins(18, 16, 18, 16)
        chl.setSpacing(10)

        cl_title_row = QHBoxLayout()
        cl_title = QLabel("Open in ChromaLyric", cl_box)
        cl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f1f5f9;")
        cl_title_row.addWidget(cl_title)
        cl_title_row.addStretch()

        self._adv_toggle_btn = QPushButton("▸ Advanced Options", cl_box)
        self._adv_toggle_btn.setCheckable(True)
        self._adv_toggle_btn.setChecked(False)
        self._adv_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adv_toggle_btn.setToolTip("Show optional ChromaLyric CLI styling & geometry flags")
        self._adv_toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #94a3b8; border: 1px solid #334155; "
            "border-radius: 4px; font-size: 11px; padding: 3px 9px; }"
            "QPushButton:hover { color: #e2e8f0; border-color: #475569; }"
            "QPushButton:checked { color: #93c5fd; border-color: #2563eb; background-color: #172536; }"
        )
        self._adv_toggle_btn.toggled.connect(self._on_advanced_toggled)
        cl_title_row.addWidget(self._adv_toggle_btn)
        chl.addLayout(cl_title_row)

        cl_desc = QLabel(
            "Open the converted .ass file directly in ChromaLyric for visual styling and rendering. "
            "Expand Advanced Options to apply CLI flags automatically before opening.",
            cl_box,
        )
        cl_desc.setWordWrap(True)
        cl_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        chl.addWidget(cl_desc)

        # Collapsible Advanced CLI Options panel
        self._adv_panel = QFrame(cl_box)
        self._adv_panel.setObjectName("advBox")
        adv_l = QVBoxLayout(self._adv_panel)
        adv_l.setContentsMargins(14, 12, 14, 12)
        adv_l.setSpacing(10)

        theme_hdr = QLabel("Color & Theme Styling", self._adv_panel)
        theme_hdr.setStyleSheet("font-size: 12px; font-weight: bold; color: #cbd5e1;")
        adv_l.addWidget(theme_hdr)

        theme_grid = QGridLayout()
        theme_grid.setHorizontalSpacing(10)
        theme_grid.setVerticalSpacing(10)
        theme_grid.setColumnStretch(1, 1)

        self._theme_group = QButtonGroup(self)

        self._radio_none = QRadioButton("Default (No Color Changes)", self._adv_panel)
        self._radio_none.setChecked(True)
        self._theme_group.addButton(self._radio_none)
        theme_grid.addWidget(self._radio_none, 0, 0, 1, 2)

        self._radio_preset = QRadioButton("Apply Preset:", self._adv_panel)
        self._theme_group.addButton(self._radio_preset)
        self._preset_combo = QComboBox(self._adv_panel)
        self._preset_combo.setMinimumHeight(28)
        self._preset_combo.setEnabled(False)
        self._radio_preset.toggled.connect(self._preset_combo.setEnabled)
        theme_grid.addWidget(self._radio_preset, 1, 0)
        theme_grid.addWidget(self._preset_combo, 1, 1)

        self._radio_extract = QRadioButton("Extract Theme:", self._adv_panel)
        self._theme_group.addButton(self._radio_extract)
        extract_row = QHBoxLayout()
        extract_row.setContentsMargins(0, 0, 0, 0)
        extract_row.setSpacing(6)
        self._extract_edit = QLineEdit(self._adv_panel)
        self._extract_edit.setMinimumHeight(28)
        self._extract_edit.setPlaceholderText("Select cover / theme image…")
        self._extract_edit.setEnabled(False)
        self._extract_browse_btn = QPushButton("Browse…", self._adv_panel)
        self._extract_browse_btn.setMinimumHeight(28)
        self._extract_browse_btn.setEnabled(False)
        self._extract_browse_btn.clicked.connect(self._browse_extract_image)
        self._radio_extract.toggled.connect(self._extract_edit.setEnabled)
        self._radio_extract.toggled.connect(self._extract_browse_btn.setEnabled)
        extract_row.addWidget(self._extract_edit, 1)
        extract_row.addWidget(self._extract_browse_btn)
        theme_grid.addWidget(self._radio_extract, 2, 0)
        theme_grid.addLayout(extract_row, 2, 1)

        self._radio_randomize = QRadioButton("Randomize Colors", self._adv_panel)
        self._theme_group.addButton(self._radio_randomize)
        theme_grid.addWidget(self._radio_randomize, 3, 0, 1, 2)

        adv_l.addLayout(theme_grid)

        geom_hdr = QLabel("Geometry & Sizing", self._adv_panel)
        geom_hdr.setStyleSheet("font-size: 12px; font-weight: bold; color: #cbd5e1; margin-top: 4px;")
        adv_l.addWidget(geom_hdr)

        geom_row = QHBoxLayout()
        geom_row.setSpacing(14)
        self._chk_safe_size = QCheckBox("Auto Safe Size", self._adv_panel)
        self._chk_pseudo_3d = QCheckBox("Pseudo-3D", self._adv_panel)
        self._chk_layers = QCheckBox("Shadow Layers:", self._adv_panel)
        self._spin_layers = QSpinBox(self._adv_panel)
        self._spin_layers.setRange(1, 15)
        self._spin_layers.setValue(5)
        self._spin_layers.setMinimumHeight(26)
        self._spin_layers.setFixedWidth(58)
        self._spin_layers.setEnabled(False)
        self._chk_layers.toggled.connect(self._spin_layers.setEnabled)
        self._chk_pseudo_3d.toggled.connect(self._on_pseudo_3d_toggled)

        geom_row.addWidget(self._chk_safe_size)
        geom_row.addWidget(self._chk_pseudo_3d)
        geom_row.addWidget(self._chk_layers)
        geom_row.addWidget(self._spin_layers)
        geom_row.addStretch()
        adv_l.addLayout(geom_row)

        adv_note = QLabel(
            "CLI flags are only passed if you modify an option above. "
            "Use 'Just Open (Skip CLI)' to launch directly without applying CLI flags.",
            self._adv_panel,
        )
        adv_note.setWordWrap(True)
        adv_note.setStyleSheet("font-size: 11px; color: #64748b;")
        adv_l.addWidget(adv_note)

        self._adv_panel.hide()
        chl.addWidget(self._adv_panel)

        # ChromaLyric action buttons
        cl_btn_row = QHBoxLayout()
        cl_btn_row.setSpacing(8)

        self._open_cl_btn = QPushButton("Open in ChromaLyric", cl_box)
        self._open_cl_btn.setMinimumHeight(36)
        self._open_cl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_cl_btn.setStyleSheet(
            "QPushButton { background-color: #1d4ed8; color: #ffffff; font-size: 13px; "
            "font-weight: bold; border: none; border-radius: 5px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #2563eb; }"
            "QPushButton:pressed { background-color: #1e40af; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self._open_cl_btn.setEnabled(False)
        self._open_cl_btn.clicked.connect(lambda: self._on_open_in_chromalyric(skip_cli=False))
        cl_btn_row.addWidget(self._open_cl_btn, 1)

        self._skip_cli_btn = QPushButton("Just Open (Skip CLI)", cl_box)
        self._skip_cli_btn.setMinimumHeight(36)
        self._skip_cli_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_cli_btn.setToolTip("Skip all Advanced CLI options and open the .ass file directly in ChromaLyric")
        self._skip_cli_btn.setVisible(False)
        self._skip_cli_btn.setEnabled(False)
        self._skip_cli_btn.clicked.connect(lambda: self._on_open_in_chromalyric(skip_cli=True))
        cl_btn_row.addWidget(self._skip_cli_btn)

        chl.addLayout(cl_btn_row)

        self._cl_status_lbl = QLabel("", cl_box)
        self._cl_status_lbl.setWordWrap(True)
        self._cl_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #94a3b8; padding: 8px 10px; "
            "border-radius: 5px; background-color: #1e293b; border: 1px solid #334155; }"
        )
        self._cl_status_lbl.hide()
        chl.addWidget(self._cl_status_lbl)

        cl.addWidget(cl_box)

        # --- Section 3: Make Video (FFmpeg MP4 / Transparent MOV) ---
        vid_box = QFrame(card)
        vid_box.setObjectName("sectionBox")
        vl = QVBoxLayout(vid_box)
        vl.setContentsMargins(18, 16, 18, 16)
        vl.setSpacing(10)

        vid_title = QLabel("Make Video", vid_box)
        vid_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f1f5f9;")
        vl.addWidget(vid_title)

        vid_desc = QLabel(
            "Render a 1080p 60 fps karaoke video (.mp4, or transparent ProRes .mov) from your "
            ".ass subtitles and instrumental audio track using FFmpeg.",
            vid_box,
        )
        vid_desc.setWordWrap(True)
        vid_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        vl.addWidget(vid_desc)

        vid_grid = QGridLayout()
        vid_grid.setHorizontalSpacing(10)
        vid_grid.setVerticalSpacing(8)
        vid_grid.setColumnStretch(1, 1)

        v_ass_lbl = QLabel("Subtitles (.ass):", vid_box)
        v_ass_lbl.setStyleSheet(lbl_style)
        self._vid_ass_edit = QLineEdit(vid_box)
        self._vid_ass_edit.setMinimumHeight(28)
        self._vid_ass_edit.setPlaceholderText("Auto-filled after conversion, or browse for another .ass…")
        self._vid_ass_browse = QPushButton("Browse…", vid_box)
        self._vid_ass_browse.setMinimumHeight(28)
        self._vid_ass_browse.clicked.connect(self._browse_video_ass)
        vid_grid.addWidget(v_ass_lbl, 0, 0)
        vid_grid.addWidget(self._vid_ass_edit, 0, 1)
        vid_grid.addWidget(self._vid_ass_browse, 0, 2)

        v_aud_lbl = QLabel("Instrumental Audio:", vid_box)
        v_aud_lbl.setStyleSheet(lbl_style)
        self._vid_audio_edit = QLineEdit(vid_box)
        self._vid_audio_edit.setMinimumHeight(28)
        self._vid_audio_edit.setPlaceholderText("Select instrumental audio track (.mp3, .flac, .wav…)…")
        self._vid_audio_browse = QPushButton("Browse…", vid_box)
        self._vid_audio_browse.setMinimumHeight(28)
        self._vid_audio_browse.clicked.connect(self._browse_video_audio)
        vid_grid.addWidget(v_aud_lbl, 1, 0)
        vid_grid.addWidget(self._vid_audio_edit, 1, 1)
        vid_grid.addWidget(self._vid_audio_browse, 1, 2)

        v_out_lbl = QLabel("Output Video:", vid_box)
        v_out_lbl.setStyleSheet(lbl_style)
        self._vid_out_edit = QLineEdit(vid_box)
        self._vid_out_edit.setMinimumHeight(28)
        self._vid_out_edit.setPlaceholderText("Defaults to the .ass directory…")
        self._vid_out_browse = QPushButton("Browse…", vid_box)
        self._vid_out_browse.setMinimumHeight(28)
        self._vid_out_browse.clicked.connect(self._browse_video_output)
        vid_grid.addWidget(v_out_lbl, 2, 0)
        vid_grid.addWidget(self._vid_out_edit, 2, 1)
        vid_grid.addWidget(self._vid_out_browse, 2, 2)

        vl.addLayout(vid_grid)

        vid_opts_row = QHBoxLayout()
        vid_opts_row.setSpacing(12)

        bg_lbl = QLabel("Background:", vid_box)
        bg_lbl.setStyleSheet(lbl_style)
        self._vid_bg_swatch = QFrame(vid_box)
        self._vid_bg_swatch.setFixedSize(18, 18)
        self._vid_bg_swatch.setStyleSheet("background-color: #000000; border: 1px solid #64748b; border-radius: 3px;")
        self._vid_bg_edit = QLineEdit("#000000", vid_box)
        self._vid_bg_edit.setReadOnly(True)
        self._vid_bg_edit.setFixedWidth(78)
        self._vid_bg_edit.setMinimumHeight(26)
        self._vid_bg_edit.setToolTip("Background color read automatically from the selected .ass file")

        self._vid_transparent_cb = QCheckBox("Transparent BG (.mov)", vid_box)
        self._vid_transparent_cb.setToolTip(
            "Render a transparent ProRes 4444 .mov (auto-checked if the .ass file uses transparency)"
        )

        vid_opts_row.addWidget(bg_lbl)
        vid_opts_row.addWidget(self._vid_bg_swatch)
        vid_opts_row.addWidget(self._vid_bg_edit)
        vid_opts_row.addSpacing(10)
        vid_opts_row.addWidget(self._vid_transparent_cb)
        vid_opts_row.addStretch()
        vl.addLayout(vid_opts_row)

        vid_btn_row = QHBoxLayout()
        vid_btn_row.setSpacing(8)

        self._make_video_btn = QPushButton("Make Video (MP4)", vid_box)
        self._make_video_btn.setMinimumHeight(36)
        self._make_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._make_video_btn.setStyleSheet(
            "QPushButton { background-color: #15803d; color: #ffffff; font-size: 13px; "
            "font-weight: bold; border: none; border-radius: 5px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #16a34a; }"
            "QPushButton:pressed { background-color: #166534; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self._make_video_btn.setEnabled(False)
        self._make_video_btn.clicked.connect(self._on_make_video)
        vid_btn_row.addWidget(self._make_video_btn, 1)

        self._cancel_video_btn = QPushButton("Cancel", vid_box)
        self._cancel_video_btn.setMinimumHeight(36)
        self._cancel_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_video_btn.setStyleSheet(
            "QPushButton { background-color: #b91c1c; color: #ffffff; font-size: 12px; "
            "font-weight: bold; border: none; border-radius: 5px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #dc2626; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self._cancel_video_btn.setVisible(False)
        self._cancel_video_btn.clicked.connect(self._on_cancel_video)
        vid_btn_row.addWidget(self._cancel_video_btn)

        self._open_video_folder_btn = QPushButton("Open Video Folder", vid_box)
        self._open_video_folder_btn.setMinimumHeight(36)
        self._open_video_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_video_folder_btn.setVisible(False)
        self._open_video_folder_btn.clicked.connect(self._on_open_video_folder)
        vid_btn_row.addWidget(self._open_video_folder_btn)

        vl.addLayout(vid_btn_row)

        self._vid_status_lbl = QLabel("", vid_box)
        self._vid_status_lbl.setWordWrap(True)
        self._vid_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #94a3b8; padding: 8px 10px; "
            "border-radius: 5px; background-color: #1e293b; border: 1px solid #334155; }"
        )
        self._vid_status_lbl.hide()
        vl.addWidget(self._vid_status_lbl)

        cl.addWidget(vid_box)

        # --- Section 4: Prepend Title Card (Fast Stream-Copy Concat) ---
        tc_box = QFrame(card)
        tc_box.setObjectName("sectionBox")
        tcl = QVBoxLayout(tc_box)
        tcl.setContentsMargins(18, 16, 18, 16)
        tcl.setSpacing(10)

        tc_title = QLabel("Prepend Title Card", tc_box)
        tc_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f1f5f9;")
        tcl.addWidget(tc_title)

        tc_desc = QLabel(
            "Add a title card image before your rendered video without re-encoding the karaoke song.",
            tc_box,
        )
        tc_desc.setWordWrap(True)
        tc_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        tcl.addWidget(tc_desc)

        tc_grid = QGridLayout()
        tc_grid.setHorizontalSpacing(10)
        tc_grid.setVerticalSpacing(8)
        tc_grid.setColumnStretch(1, 1)

        tc_vid_lbl = QLabel("Video File:", tc_box)
        tc_vid_lbl.setStyleSheet(lbl_style)
        self._tc_video_edit = QLineEdit(tc_box)
        self._tc_video_edit.setMinimumHeight(28)
        self._tc_video_edit.setPlaceholderText("Auto-filled after Make Video, or browse for .mp4 / .mov…")
        self._tc_video_browse = QPushButton("Browse…", tc_box)
        self._tc_video_browse.setMinimumHeight(28)
        self._tc_video_browse.clicked.connect(self._browse_tc_video)
        tc_grid.addWidget(tc_vid_lbl, 0, 0)
        tc_grid.addWidget(self._tc_video_edit, 0, 1)
        tc_grid.addWidget(self._tc_video_browse, 0, 2)

        tc_img_lbl = QLabel("Title Card Image:", tc_box)
        tc_img_lbl.setStyleSheet(lbl_style)
        self._tc_img_edit = QLineEdit(tc_box)
        self._tc_img_edit.setMinimumHeight(28)
        self._tc_img_edit.setPlaceholderText("Select title card picture (.png, .jpg, .webp…)…")
        self._tc_img_browse = QPushButton("Browse…", tc_box)
        self._tc_img_browse.setMinimumHeight(28)
        self._tc_img_browse.clicked.connect(self._browse_tc_image)
        tc_grid.addWidget(tc_img_lbl, 1, 0)
        tc_grid.addWidget(self._tc_img_edit, 1, 1)
        tc_grid.addWidget(self._tc_img_browse, 1, 2)

        tc_out_lbl = QLabel("Output Video:", tc_box)
        tc_out_lbl.setStyleSheet(lbl_style)
        self._tc_out_edit = QLineEdit(tc_box)
        self._tc_out_edit.setMinimumHeight(28)
        self._tc_out_edit.setPlaceholderText("Defaults beside the video file…")
        self._tc_out_browse = QPushButton("Browse…", tc_box)
        self._tc_out_browse.setMinimumHeight(28)
        self._tc_out_browse.clicked.connect(self._browse_tc_output)
        tc_grid.addWidget(tc_out_lbl, 2, 0)
        tc_grid.addWidget(self._tc_out_edit, 2, 1)
        tc_grid.addWidget(self._tc_out_browse, 2, 2)

        tcl.addLayout(tc_grid)

        tc_dur_row = QHBoxLayout()
        tc_dur_row.setSpacing(10)
        tc_dur_lbl = QLabel("Title Card Duration:", tc_box)
        tc_dur_lbl.setStyleSheet(lbl_style)
        self._tc_dur_spin = QDoubleSpinBox(tc_box)
        self._tc_dur_spin.setRange(0.5, 30.0)
        self._tc_dur_spin.setSingleStep(0.5)
        self._tc_dur_spin.setDecimals(1)
        self._tc_dur_spin.setValue(3.0)
        self._tc_dur_spin.setSuffix(" s")
        self._tc_dur_spin.setMinimumHeight(26)
        self._tc_dur_spin.setFixedWidth(84)
        tc_dur_row.addWidget(tc_dur_lbl)
        tc_dur_row.addWidget(self._tc_dur_spin)
        tc_dur_row.addStretch()
        tcl.addLayout(tc_dur_row)

        tc_btn_row = QHBoxLayout()
        tc_btn_row.setSpacing(8)

        self._prepend_tc_btn = QPushButton("Prepend Title Card", tc_box)
        self._prepend_tc_btn.setMinimumHeight(36)
        self._prepend_tc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prepend_tc_btn.setStyleSheet(
            "QPushButton { background-color: #1d4ed8; color: #ffffff; font-size: 13px; "
            "font-weight: bold; border: none; border-radius: 5px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #2563eb; }"
            "QPushButton:pressed { background-color: #1e40af; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self._prepend_tc_btn.setEnabled(False)
        self._prepend_tc_btn.clicked.connect(self._on_prepend_title_card)
        tc_btn_row.addWidget(self._prepend_tc_btn, 1)

        self._open_tc_folder_btn = QPushButton("Open Output Folder", tc_box)
        self._open_tc_folder_btn.setMinimumHeight(36)
        self._open_tc_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_tc_folder_btn.setVisible(False)
        self._open_tc_folder_btn.clicked.connect(self._on_open_tc_folder)
        tc_btn_row.addWidget(self._open_tc_folder_btn)

        tcl.addLayout(tc_btn_row)

        self._tc_status_lbl = QLabel("", tc_box)
        self._tc_status_lbl.setWordWrap(True)
        self._tc_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #94a3b8; padding: 8px 10px; "
            "border-radius: 5px; background-color: #1e293b; border: 1px solid #334155; }"
        )
        self._tc_status_lbl.hide()
        tcl.addWidget(self._tc_status_lbl)

        cl.addWidget(tc_box)
        root.addWidget(card)

        # Title card signals
        self._tc_video_edit.textChanged.connect(self._on_tc_video_changed)
        self._tc_img_edit.textChanged.connect(self._update_tc_buttons)
        self._tc_out_edit.textEdited.connect(self._on_tc_out_edited)
        self._tc_out_edit.textChanged.connect(self._update_tc_buttons)

        # Video field signals
        self._vid_ass_edit.textChanged.connect(self._on_video_ass_text_changed)
        self._vid_audio_edit.textChanged.connect(self._update_video_buttons)
        self._vid_out_edit.textEdited.connect(self._on_video_out_edited)
        self._vid_out_edit.textChanged.connect(self._update_video_buttons)
        self._vid_transparent_cb.toggled.connect(self._on_video_transparent_toggled)

        # Keep primary button label in sync when CLI options change
        for w in (
            self._radio_none,
            self._radio_preset,
            self._radio_extract,
            self._radio_randomize,
            self._chk_safe_size,
            self._chk_pseudo_3d,
            self._chk_layers,
        ):
            w.toggled.connect(self._update_chromalyric_buttons)
        self._preset_combo.currentIndexChanged.connect(self._update_chromalyric_buttons)
        self._extract_edit.textChanged.connect(self._update_chromalyric_buttons)
        self._spin_layers.valueChanged.connect(self._update_chromalyric_buttons)

        self._load_chromalyric_presets()
        self._update_chromalyric_buttons()
        self._update_video_buttons()
        self._update_tc_buttons()

    def _load_chromalyric_presets(self) -> None:
        presets: List[str] = []
        try:
            cl_settings = QSettings("MattJoy", "ChromaLyric")
            saved_data = str(cl_settings.value("theme_presets", "[]"))
            parsed = json.loads(saved_data)
            if isinstance(parsed, list):
                presets = [str(p.get("name", "")).strip() for p in parsed if isinstance(p, dict) and p.get("name")]
        except Exception:
            presets = []

        current = self._preset_combo.currentText()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItems(presets)
        if current and current in presets:
            self._preset_combo.setCurrentText(current)
        self._preset_combo.blockSignals(False)

    def _on_advanced_toggled(self, checked: bool) -> None:
        self._adv_toggle_btn.setText("▾ Advanced Options" if checked else "▸ Advanced Options")
        self._adv_panel.setVisible(checked)
        if checked:
            self._load_chromalyric_presets()
        self._update_chromalyric_buttons()
        self._update_video_buttons()
        self._update_tc_buttons()

    def _on_pseudo_3d_toggled(self, checked: bool) -> None:
        if checked:
            self._chk_layers.setChecked(True)

    def _browse_extract_image(self) -> None:
        start_dir = ""
        if self._last_ass_path is not None:
            start_dir = str(self._last_ass_path.parent)
        elif self.project is not None and self.project.source_path is not None:
            start_dir = str(self.project.source_path.parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Theme Image",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All Files (*.*)",
        )
        if path:
            self._extract_edit.setText(path)

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

    def current_chromalyric_options(self) -> ChromaLyricCliOptions:
        if self._radio_preset.isChecked():
            theme_mode = "preset"
        elif self._radio_extract.isChecked():
            theme_mode = "extract"
        elif self._radio_randomize.isChecked():
            theme_mode = "randomize"
        else:
            theme_mode = "none"
        return ChromaLyricCliOptions(
            theme_mode=theme_mode,
            preset_name=self._preset_combo.currentText().strip(),
            extract_image_path=self._extract_edit.text().strip(),
            auto_safe_size=self._chk_safe_size.isChecked(),
            pseudo_3d=self._chk_pseudo_3d.isChecked(),
            shadow_layers=int(self._spin_layers.value()) if self._chk_layers.isChecked() else None,
        )

    def _active_ass_path(self) -> Optional[Path]:
        if self._last_ass_path is not None and self._last_ass_path.is_file():
            return self._last_ass_path
        if self.project is not None and self.project.source_path is not None:
            cand = self.project.source_path.with_suffix(".ass")
            if cand.is_file():
                return cand
        return None

    def _update_chromalyric_buttons(self) -> None:
        ass_path = self._active_ass_path()
        busy = self._cl_worker is not None and self._cl_worker.isRunning()
        has_ass = ass_path is not None
        adv_open = self._adv_toggle_btn.isChecked()
        has_cli_mods = adv_open and self.current_chromalyric_options().has_modifications

        self._open_cl_btn.setEnabled(has_ass and not busy)
        if busy:
            self._open_cl_btn.setText("Launching ChromaLyric…")
        elif has_cli_mods:
            self._open_cl_btn.setText("Apply CLI && Open in ChromaLyric")
        else:
            self._open_cl_btn.setText("Open in ChromaLyric")

        if has_ass:
            self._open_cl_btn.setToolTip(
                f"Apply modified CLI flags and open {ass_path.name} in ChromaLyric"
                if has_cli_mods
                else f"Open {ass_path.name} directly in ChromaLyric"
            )
        else:
            self._open_cl_btn.setToolTip("Convert your project to .ass above first")

        self._skip_cli_btn.setVisible(adv_open)
        self._skip_cli_btn.setEnabled(has_ass and not busy)

    def set_project(self, project: Optional[KbpProject]) -> None:
        is_new_project = project is not self.project
        self.project = project
        if is_new_project:
            self._last_ass_path = None
            self._last_video_path = None
            self._last_tc_video_path = None
            self._custom_video_out = False
            self._custom_tc_out = False
            self._open_folder_btn.setVisible(False)
            self._open_video_folder_btn.setVisible(False)
            self._open_tc_folder_btn.setVisible(False)
            self._status_lbl.hide()
            self._cl_status_lbl.hide()
            self._vid_status_lbl.hide()
            self._tc_status_lbl.hide()
            self._vid_ass_edit.blockSignals(True)
            self._vid_ass_edit.clear()
            self._vid_ass_edit.blockSignals(False)
            self._watch_video_ass(None)
            self._vid_audio_edit.clear()
            self._vid_out_edit.clear()
            self._tc_video_edit.clear()
            self._tc_img_edit.clear()
            self._tc_out_edit.clear()

        if project is None:
            self._project_lbl.setText("No project loaded.")
            self._source_lbl.hide()
            self._convert_btn.setEnabled(False)
            self._convert_btn.setToolTip("Load or sync a KBP project first")
            self._update_chromalyric_buttons()
            self._update_video_buttons()
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
        self._update_chromalyric_buttons()
        existing_ass = self._active_ass_path()
        if existing_ass is not None and not self._vid_ass_edit.text().strip():
            self._set_video_ass_path(existing_ass)
        self._update_video_buttons()

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
        self._update_chromalyric_buttons()
        self._set_video_ass_path(saved)
        self.status_message.emit(f"Converted to {saved.name} ({saved.parent})", 6000)

    def _on_open_output_folder(self) -> None:
        if self._last_ass_path is None:
            return
        if sys.platform == "win32" and self._last_ass_path.exists():
            subprocess.Popen(["explorer", f"/select,{self._last_ass_path}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_ass_path.parent)))

    def _prompt_missing_chromalyric(self) -> str:
        """
        Inform the user ChromaLyric wasn't found and return:
        'locate', 'download', or 'cancel'.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("ChromaLyric Not Found")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "ChromaLyric was not found in the default install location.<br><br>"
            "If you have it installed elsewhere, click <b>Locate .exe…</b> to select "
            "<code>ChromaLyric.exe</code>.<br><br>"
            "Need to install it? Download it from GitHub:<br>"
            f'<a href="{CHROMALYRIC_DOWNLOAD_URL}">{CHROMALYRIC_DOWNLOAD_URL}</a>'
        )
        locate_btn = box.addButton("Locate .exe…", QMessageBox.ButtonRole.AcceptRole)
        download_btn = box.addButton("Open Download Page", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(locate_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is locate_btn:
            return "locate"
        if clicked is download_btn:
            return "download"
        return "cancel"

    def _resolve_chromalyric_exe(self) -> Optional[Path]:
        saved = self._settings.value(self._CL_SETTINGS_KEY, "", type=str)
        found = find_chromalyric_exe(configured_path=saved)
        if found is not None:
            return found

        action = self._prompt_missing_chromalyric()
        if action == "download":
            QDesktopServices.openUrl(QUrl(CHROMALYRIC_DOWNLOAD_URL))
            return None
        if action != "locate":
            return None

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate ChromaLyric.exe",
            r"C:\Program Files",
            "Executable Files (*.exe);;All Files (*.*)",
        )
        if not path:
            return None
        chosen = Path(path)
        if not chosen.is_file():
            QMessageBox.warning(self, "Invalid File", f"File not found:\n{chosen}")
            return None
        self._settings.setValue(self._CL_SETTINGS_KEY, str(chosen))
        self._settings.sync()
        return chosen

    def _on_open_in_chromalyric(self, *, skip_cli: bool = False) -> None:
        ass_path = self._active_ass_path()
        if ass_path is None:
            return

        exe_path = self._resolve_chromalyric_exe()
        if exe_path is None:
            return

        opts: Optional[ChromaLyricCliOptions] = None
        if not skip_cli and self._adv_toggle_btn.isChecked():
            candidate = self.current_chromalyric_options()
            if candidate.has_modifications:
                opts = candidate

        self._cl_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #93c5fd; padding: 8px 10px; "
            "border-radius: 5px; background-color: #172536; border: 1px solid #2563eb; }"
        )
        self._cl_status_lbl.setText(
            "Running ChromaLyric CLI and opening…" if opts is not None else "Opening in ChromaLyric…"
        )
        self._cl_status_lbl.show()

        self._cl_worker = _ChromaLyricWorker(
            exe_path=exe_path,
            ass_path=ass_path,
            options=opts,
            parent=self,
        )
        self._cl_worker.succeeded.connect(self._on_chromalyric_succeeded)
        self._cl_worker.failed.connect(self._on_chromalyric_failed)
        self._cl_worker.finished.connect(self._update_chromalyric_buttons)
        self._cl_worker.start()
        self._update_chromalyric_buttons()

    def _on_chromalyric_succeeded(self, msg: str) -> None:
        self._cl_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #bbf7d0; padding: 8px 10px; "
            "border-radius: 5px; background-color: #052e16; border: 1px solid #166534; }"
        )
        self._cl_status_lbl.setText(f"✓ {msg}")
        self._cl_status_lbl.show()
        self.status_message.emit(msg, 6000)

    def _on_chromalyric_failed(self, error_msg: str) -> None:
        self._cl_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #fecaca; padding: 8px 10px; "
            "border-radius: 5px; background-color: #450a0a; border: 1px solid #991b1b; }"
        )
        self._cl_status_lbl.setText(f"ChromaLyric launch failed: {error_msg}")
        self._cl_status_lbl.show()
        self.status_message.emit(f"ChromaLyric failed: {error_msg}", 6000)

    # --- Make Video (FFmpeg) ---

    def _set_video_bg_color(self, hex_color: str) -> None:
        clean = hex_color if (hex_color and hex_color.startswith("#")) else "#000000"
        self._vid_bg_edit.setText(clean)
        self._vid_bg_swatch.setStyleSheet(
            f"background-color: {clean}; border: 1px solid #64748b; border-radius: 3px;"
        )

    def _watch_video_ass(self, ass_path: Optional[Path]) -> None:
        """Monitor the active .ass file (and its parent folder for atomic saves) for external edits."""
        existing_files = self._ass_watcher.files()
        if existing_files:
            self._ass_watcher.removePaths(existing_files)
        existing_dirs = self._ass_watcher.directories()
        if existing_dirs:
            self._ass_watcher.removePaths(existing_dirs)

        self._watched_ass_path = ass_path
        self._last_ass_stat = None
        if ass_path is None:
            self._ass_poll_timer.stop()
            return

        if ass_path.parent.is_dir():
            self._ass_watcher.addPath(str(ass_path.parent))
        if ass_path.is_file():
            self._ass_watcher.addPath(str(ass_path))
            st = ass_path.stat()
            self._last_ass_stat = (str(ass_path), st.st_mtime_ns, st.st_size)
        if not self._ass_poll_timer.isActive():
            self._ass_poll_timer.start()

    def _schedule_ass_refresh(self, _changed_path: str = "") -> None:
        if self._watched_ass_path is not None:
            self._ass_refresh_timer.start()

    def _check_watched_ass_changed(self) -> None:
        ass_path = self._watched_ass_path
        if ass_path is None or not ass_path.is_file():
            return
        try:
            st = ass_path.stat()
        except OSError:
            return
        sig = (str(ass_path), st.st_mtime_ns, st.st_size)
        # Re-add file to watcher if an external app saved via atomic replace
        if str(ass_path) not in self._ass_watcher.files():
            self._ass_watcher.addPath(str(ass_path))
        if sig != self._last_ass_stat:
            self._last_ass_stat = sig
            self._sync_video_metadata_from_ass(ass_path, preserve_custom_out=True)
            self._update_video_buttons()

    def _set_video_ass_path(self, ass_path: Path) -> None:
        self._vid_ass_edit.blockSignals(True)
        self._vid_ass_edit.setText(str(ass_path))
        self._vid_ass_edit.blockSignals(False)
        self._sync_video_metadata_from_ass(ass_path)
        self._watch_video_ass(ass_path)
        self._update_video_buttons()

    def _sync_video_metadata_from_ass(self, ass_path: Path, *, preserve_custom_out: bool = False) -> None:
        if not ass_path.is_file():
            return
        bg = parse_ass_background_color(ass_path) or "#000000"
        self._set_video_bg_color(bg)
        is_trans = check_ass_should_be_transparent(ass_path)
        self._vid_transparent_cb.blockSignals(True)
        self._vid_transparent_cb.setChecked(is_trans)
        self._vid_transparent_cb.blockSignals(False)
        ext = ".mov" if is_trans else ".mp4"
        cur_out = self._vid_out_edit.text().strip()
        if not self._custom_video_out or not cur_out:
            self._vid_out_edit.blockSignals(True)
            self._vid_out_edit.setText(str(ass_path.with_suffix(ext)))
            self._vid_out_edit.blockSignals(False)
        elif preserve_custom_out and cur_out:
            p_out = Path(cur_out)
            if p_out.suffix.lower() in (".mp4", ".mov") and p_out.suffix.lower() != ext:
                self._vid_out_edit.blockSignals(True)
                self._vid_out_edit.setText(str(p_out.with_suffix(ext)))
                self._vid_out_edit.blockSignals(False)

    def _on_video_ass_text_changed(self, text: str) -> None:
        p = Path(text.strip()) if text.strip() else None
        if p is not None and p.is_file():
            self._sync_video_metadata_from_ass(p)
            self._watch_video_ass(p)
        else:
            self._watch_video_ass(None)
        self._update_video_buttons()

    def _on_video_out_edited(self, _text: str) -> None:
        self._custom_video_out = True

    def _on_video_transparent_toggled(self, checked: bool) -> None:
        expected_ext = ".mov" if checked else ".mp4"
        cur_out = self._vid_out_edit.text().strip()
        if cur_out:
            p = Path(cur_out)
            if p.suffix.lower() in (".mp4", ".mov") and p.suffix.lower() != expected_ext:
                self._vid_out_edit.blockSignals(True)
                self._vid_out_edit.setText(str(p.with_suffix(expected_ext)))
                self._vid_out_edit.blockSignals(False)
        self._update_video_buttons()

    def _video_default_dir(self) -> str:
        ass_txt = self._vid_ass_edit.text().strip()
        if ass_txt and Path(ass_txt).parent.exists():
            return str(Path(ass_txt).parent)
        if self._last_ass_path is not None and self._last_ass_path.parent.exists():
            return str(self._last_ass_path.parent)
        if self.project is not None and self.project.source_path is not None:
            return str(self.project.source_path.parent)
        return ""

    def _browse_video_ass(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Subtitles (.ass)",
            self._video_default_dir(),
            "SubStation Alpha Subtitles (*.ass);;All Files (*.*)",
        )
        if path:
            self._custom_video_out = False
            self._set_video_ass_path(Path(path))

    def _browse_video_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Instrumental Audio Track",
            self._video_default_dir(),
            "Audio Files (*.mp3 *.flac *.wav *.opus *.m4a *.aac *.ogg);;All Files (*.*)",
        )
        if path:
            self._vid_audio_edit.setText(path)

    def _browse_video_output(self) -> None:
        is_trans = self._vid_transparent_cb.isChecked()
        ext = ".mov" if is_trans else ".mp4"
        filt = "QuickTime ProRes Video (*.mov)" if is_trans else "MP4 Video (*.mp4)"
        default_target = self._vid_out_edit.text().strip() or self._video_default_dir()
        path, _ = QFileDialog.getSaveFileName(self, "Save Output Video As", default_target, filt)
        if path:
            out_p = Path(path)
            if out_p.suffix.lower() != ext:
                out_p = out_p.with_suffix(ext)
            self._custom_video_out = True
            self._vid_out_edit.setText(str(out_p))

    def _update_video_buttons(self) -> None:
        ass_txt = self._vid_ass_edit.text().strip()
        aud_txt = self._vid_audio_edit.text().strip()
        out_txt = self._vid_out_edit.text().strip()
        is_trans = self._vid_transparent_cb.isChecked()
        fmt_lbl = "MOV" if is_trans else "MP4"
        busy = self._video_worker is not None and self._video_worker.isRunning()
        ready = bool(ass_txt and aud_txt and out_txt and Path(ass_txt).is_file() and Path(aud_txt).is_file())

        self._make_video_btn.setText(f"Rendering {fmt_lbl}…" if busy else f"Make Video ({fmt_lbl})")
        self._make_video_btn.setEnabled(ready and not busy)
        self._cancel_video_btn.setVisible(busy)
        self._cancel_video_btn.setEnabled(busy)

        for w in (
            self._vid_ass_edit,
            self._vid_ass_browse,
            self._vid_audio_edit,
            self._vid_audio_browse,
            self._vid_out_edit,
            self._vid_out_browse,
            self._vid_transparent_cb,
        ):
            w.setEnabled(not busy)

    def _prompt_missing_ffmpeg(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("FFmpeg Not Found")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "FFmpeg was not found on PATH or in standard install locations.<br><br>"
            "If you have it installed, click <b>Locate ffmpeg.exe…</b> to select it.<br><br>"
            "Need to download FFmpeg? Get a Windows build here:<br>"
            f'<a href="{FFMPEG_DOWNLOAD_URL}">{FFMPEG_DOWNLOAD_URL}</a>'
        )
        locate_btn = box.addButton("Locate ffmpeg.exe…", QMessageBox.ButtonRole.AcceptRole)
        download_btn = box.addButton("Open Download Page", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(locate_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is locate_btn:
            return "locate"
        if clicked is download_btn:
            return "download"
        return "cancel"

    def _resolve_ffmpeg_exe(self) -> Optional[Path]:
        saved = self._settings.value(self._FFMPEG_SETTINGS_KEY, "", type=str)
        found = find_ffmpeg_exe(configured_path=saved)
        if found is not None:
            return found

        action = self._prompt_missing_ffmpeg()
        if action == "download":
            QDesktopServices.openUrl(QUrl(FFMPEG_DOWNLOAD_URL))
            return None
        if action != "locate":
            return None

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate ffmpeg.exe",
            r"C:\ffmpeg\bin",
            "Executable Files (*.exe);;All Files (*.*)",
        )
        if not path:
            return None
        chosen = Path(path)
        if not chosen.is_file():
            QMessageBox.warning(self, "Invalid File", f"File not found:\n{chosen}")
            return None
        self._settings.setValue(self._FFMPEG_SETTINGS_KEY, str(chosen))
        self._settings.sync()
        return chosen

    def _on_make_video(self) -> None:
        ass_path = Path(self._vid_ass_edit.text().strip())
        audio_path = Path(self._vid_audio_edit.text().strip())
        out_path = Path(self._vid_out_edit.text().strip())
        if not ass_path.is_file() or not audio_path.is_file():
            return

        ffmpeg_exe = self._resolve_ffmpeg_exe()
        if ffmpeg_exe is None:
            return

        # Re-read bg color right before render in case the user edited the .ass in ChromaLyric
        bg_color = parse_ass_background_color(ass_path) or self._vid_bg_edit.text().strip() or "#000000"
        self._set_video_bg_color(bg_color)
        transparent = self._vid_transparent_cb.isChecked()
        fmt_lbl = "MOV" if transparent else "MP4"

        self._open_video_folder_btn.setVisible(False)
        self._vid_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #93c5fd; padding: 8px 10px; "
            "border-radius: 5px; background-color: #172536; border: 1px solid #2563eb; }"
        )
        self._vid_status_lbl.setText(f"Starting {fmt_lbl} video render…")
        self._vid_status_lbl.show()

        self._video_worker = _VideoRenderWorker(
            ffmpeg_exe=ffmpeg_exe,
            ass_path=ass_path,
            audio_path=audio_path,
            output_path=out_path,
            bg_color=bg_color,
            transparent=transparent,
            parent=self,
        )
        self._video_worker.progress.connect(self._on_video_progress)
        self._video_worker.succeeded.connect(self._on_video_succeeded)
        self._video_worker.failed.connect(self._on_video_failed)
        self._video_worker.finished.connect(self._update_video_buttons)
        self._video_worker.start()
        self._update_video_buttons()

    def _on_cancel_video(self) -> None:
        if self._video_worker is not None and self._video_worker.isRunning():
            self._cancel_video_btn.setEnabled(False)
            self._vid_status_lbl.setText("Cancelling video render…")
            self._video_worker.cancel()

    def _on_video_progress(self, line: str) -> None:
        short = line if len(line) <= 105 else "…" + line[-102:]
        self._vid_status_lbl.setText(short)

    def _on_video_succeeded(self, out_path_str: str) -> None:
        saved = Path(out_path_str)
        self._last_video_path = saved
        self._open_video_folder_btn.setVisible(True)
        self._vid_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #bbf7d0; padding: 8px 10px; "
            "border-radius: 5px; background-color: #052e16; border: 1px solid #166534; }"
        )
        self._vid_status_lbl.setText(f"✓ Video generated: {saved.name}\n{saved}")
        self._vid_status_lbl.show()
        self._set_tc_video_path(saved)
        self.status_message.emit(f"Video generated: {saved.name}", 6000)

    def _on_video_failed(self, error_msg: str) -> None:
        self._vid_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #fecaca; padding: 8px 10px; "
            "border-radius: 5px; background-color: #450a0a; border: 1px solid #991b1b; }"
        )
        self._vid_status_lbl.setText(error_msg)
        self._vid_status_lbl.show()
        self.status_message.emit("Video generation failed.", 6000)

    def _on_open_video_folder(self) -> None:
        if self._last_video_path is None:
            return
        if sys.platform == "win32" and self._last_video_path.exists():
            subprocess.Popen(["explorer", f"/select,{self._last_video_path}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_video_path.parent)))

    # --- Prepend Title Card ---

    def _set_tc_video_path(self, video_path: Path) -> None:
        self._tc_video_edit.setText(str(video_path))
        if not self._custom_tc_out or not self._tc_out_edit.text().strip():
            ext = video_path.suffix or ".mp4"
            self._tc_out_edit.blockSignals(True)
            self._tc_out_edit.setText(str(video_path.with_name(f"{video_path.stem} (with Title Card){ext}")))
            self._tc_out_edit.blockSignals(False)
        self._update_tc_buttons()

    def _on_tc_video_changed(self, text: str) -> None:
        p = Path(text.strip()) if text.strip() else None
        if p is not None and p.is_file() and (not self._custom_tc_out or not self._tc_out_edit.text().strip()):
            ext = p.suffix or ".mp4"
            self._tc_out_edit.blockSignals(True)
            self._tc_out_edit.setText(str(p.with_name(f"{p.stem} (with Title Card){ext}")))
            self._tc_out_edit.blockSignals(False)
        self._update_tc_buttons()

    def _on_tc_out_edited(self, _text: str) -> None:
        self._custom_tc_out = True

    def _tc_default_dir(self) -> str:
        vid_txt = self._tc_video_edit.text().strip()
        if vid_txt and Path(vid_txt).parent.exists():
            return str(Path(vid_txt).parent)
        return self._video_default_dir()

    def _browse_tc_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            self._tc_default_dir(),
            "Video Files (*.mp4 *.mov);;All Files (*.*)",
        )
        if path:
            self._custom_tc_out = False
            self._set_tc_video_path(Path(path))

    def _browse_tc_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Title Card Image",
            self._tc_default_dir(),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All Files (*.*)",
        )
        if path:
            self._tc_img_edit.setText(path)

    def _browse_tc_output(self) -> None:
        vid_txt = self._tc_video_edit.text().strip()
        ext = Path(vid_txt).suffix.lower() if vid_txt else ".mp4"
        if ext not in (".mp4", ".mov"):
            ext = ".mp4"
        filt = "QuickTime Video (*.mov)" if ext == ".mov" else "MP4 Video (*.mp4)"
        default_target = self._tc_out_edit.text().strip() or self._tc_default_dir()
        path, _ = QFileDialog.getSaveFileName(self, "Save Final Video As", default_target, filt)
        if path:
            out_p = Path(path)
            if out_p.suffix.lower() != ext:
                out_p = out_p.with_suffix(ext)
            self._custom_tc_out = True
            self._tc_out_edit.setText(str(out_p))

    def _update_tc_buttons(self) -> None:
        vid_txt = self._tc_video_edit.text().strip()
        img_txt = self._tc_img_edit.text().strip()
        out_txt = self._tc_out_edit.text().strip()
        busy = self._tc_worker is not None and self._tc_worker.isRunning()
        ready = bool(vid_txt and img_txt and out_txt and Path(vid_txt).is_file() and Path(img_txt).is_file())

        self._prepend_tc_btn.setText("Prepending Title Card…" if busy else "Prepend Title Card")
        self._prepend_tc_btn.setEnabled(ready and not busy)
        for w in (
            self._tc_video_edit,
            self._tc_video_browse,
            self._tc_img_edit,
            self._tc_img_browse,
            self._tc_out_edit,
            self._tc_out_browse,
            self._tc_dur_spin,
        ):
            w.setEnabled(not busy)

    def _on_prepend_title_card(self) -> None:
        vid_path = Path(self._tc_video_edit.text().strip())
        img_path = Path(self._tc_img_edit.text().strip())
        out_path = Path(self._tc_out_edit.text().strip())
        if not vid_path.is_file() or not img_path.is_file():
            return

        ffmpeg_exe = self._resolve_ffmpeg_exe()
        if ffmpeg_exe is None:
            return

        self._open_tc_folder_btn.setVisible(False)
        self._tc_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #93c5fd; padding: 8px 10px; "
            "border-radius: 5px; background-color: #172536; border: 1px solid #2563eb; }"
        )
        self._tc_status_lbl.setText("Preparing title card…")
        self._tc_status_lbl.show()

        self._tc_worker = _TitleCardWorker(
            ffmpeg_exe=ffmpeg_exe,
            video_path=vid_path,
            image_path=img_path,
            output_path=out_path,
            duration_sec=float(self._tc_dur_spin.value()),
            parent=self,
        )
        self._tc_worker.progress.connect(self._tc_status_lbl.setText)
        self._tc_worker.succeeded.connect(self._on_tc_succeeded)
        self._tc_worker.failed.connect(self._on_tc_failed)
        self._tc_worker.finished.connect(self._update_tc_buttons)
        self._tc_worker.start()
        self._update_tc_buttons()

    def _on_tc_succeeded(self, out_path_str: str) -> None:
        saved = Path(out_path_str)
        self._last_tc_video_path = saved
        self._open_tc_folder_btn.setVisible(True)
        self._tc_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #bbf7d0; padding: 8px 10px; "
            "border-radius: 5px; background-color: #052e16; border: 1px solid #166534; }"
        )
        self._tc_status_lbl.setText(f"✓ Title card prepended: {saved.name}\n{saved}")
        self._tc_status_lbl.show()
        self.status_message.emit(f"Title card prepended: {saved.name}", 6000)

    def _on_tc_failed(self, error_msg: str) -> None:
        self._tc_status_lbl.setStyleSheet(
            "QLabel { font-size: 12px; color: #fecaca; padding: 8px 10px; "
            "border-radius: 5px; background-color: #450a0a; border: 1px solid #991b1b; }"
        )
        self._tc_status_lbl.setText(error_msg)
        self._tc_status_lbl.show()
        self.status_message.emit("Prepending title card failed.", 6000)

    def _on_open_tc_folder(self) -> None:
        if self._last_tc_video_path is None:
            return
        if sys.platform == "win32" and self._last_tc_video_path.exists():
            subprocess.Popen(["explorer", f"/select,{self._last_tc_video_path}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_tc_video_path.parent)))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile().lower()
                if local.endswith((
                    ".ass",
                    ".mp3", ".flac", ".wav", ".opus", ".m4a", ".aac", ".ogg",
                    ".mp4", ".mov",
                    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
                )):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        handled = False
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                low = local.lower()
                if low.endswith(".ass"):
                    self._custom_video_out = False
                    self._set_video_ass_path(Path(local))
                    handled = True
                elif low.endswith((".mp3", ".flac", ".wav", ".opus", ".m4a", ".aac", ".ogg")):
                    self._vid_audio_edit.setText(local)
                    handled = True
                elif low.endswith((".mp4", ".mov")):
                    self._custom_tc_out = False
                    self._set_tc_video_path(Path(local))
                    handled = True
                elif low.endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                    self._tc_img_edit.setText(local)
                    handled = True
        if handled:
            event.acceptProposedAction()
            return
        super().dropEvent(event)
