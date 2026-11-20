from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..audio import AudioEngine
from ..history import HistoryManager
from ..model import KbpProject
from .display_view import DisplayRemoveView
from .minimap_widget import SongMinimapWidget
from .preview_widget import FloatingPreviewWindow
from .wiping_view import LyricsWipingView


def _fmt_time(cs: float) -> str:
    sec = cs / 100.0
    return f"{int(sec // 60):02d}:{sec % 60:05.2f} ({int(round(cs))} cs)"


class MainWindow(QMainWindow):
    def __init__(self, initial_file: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("Advanced KBP Editor")
        self.resize(1440, 880)

        self.project: Optional[KbpProject] = None
        self.history = HistoryManager()
        self.audio = AudioEngine(self)
        self._page_buttons: List[QPushButton] = []

        # Central container: Song Minimap + Quick Page Bar + Tabs + Transport
        central = QWidget(self)
        c_layout = QVBoxLayout(central)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(0)

        # Top Navigation Strip: Song Overview Minimap + Quick Page Jump Bar
        nav_box = QWidget(central)
        nav_box.setStyleSheet("background-color: #1b1e25; border-bottom: 1px solid #353b47;")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(8, 6, 8, 6)
        nav_layout.setSpacing(4)

        self.minimap = SongMinimapWidget(self.audio, nav_box)
        self.minimap.jump_requested.connect(self._on_minimap_jump)
        nav_layout.addWidget(self.minimap)

        self._page_bar_layout = QHBoxLayout()
        self._page_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._page_bar_layout.setSpacing(4)
        self._rebuild_page_buttons()
        nav_layout.addLayout(self._page_bar_layout)

        # Tabs: Lyrics Wiping first, Display / Remove second
        self._tabs = QTabWidget(central)
        self.wiping_view = LyricsWipingView(self.audio, self._tabs)
        self.display_view = DisplayRemoveView(self._tabs)
        self._tabs.addTab(self.wiping_view, "Lyrics Wiping (Manual Alignment)")
        self._tabs.addTab(self.display_view, "Display / Remove Window")
        self._tabs.currentChanged.connect(self._sync_minimap_from_active_tab)

        transport = self._build_transport_bar()

        c_layout.addWidget(nav_box)
        c_layout.addWidget(self._tabs, 1)
        c_layout.addWidget(transport)
        self.setCentralWidget(central)

        # Windowless (frameless) floating 16:9 Live Preview overlay — drag & resize anywhere, no docking
        self.preview_win = FloatingPreviewWindow(self)
        self.preview_win.kbp_dropped.connect(self.open_kbp)
        self._preview_positioned = False

        self.setAcceptDrops(True)

        self.setStatusBar(QStatusBar(self))
        self._settings = QSettings("MattJoy", "AdvancedKBPEditor")
        saved_auto_reset = self._settings.value("auto_reset_display", True, type=bool)
        self.wiping_view.set_auto_reset_display(saved_auto_reset)
        self.display_view.set_auto_reset_display(saved_auto_reset)
        self._build_menus()
        self._wire_signals()
        self._refresh_actions()

        if initial_file:
            self.open_kbp(initial_file)

    def _rebuild_page_buttons(self) -> None:
        while self._page_bar_layout.count():
            item = self._page_bar_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._page_buttons.clear()

        lbl = QLabel("Jump to Page:")
        lbl.setStyleSheet("color: #9aa5b1; font-size: 11px;")
        self._page_bar_layout.addWidget(lbl)

        btn_prev = QPushButton("◀")
        btn_prev.setFixedSize(26, 22)
        btn_prev.setToolTip("Previous Page")
        btn_prev.clicked.connect(lambda: self._step_page(-1))
        self._page_bar_layout.addWidget(btn_prev)

        if self.project:
            for p_idx in range(len(self.project.pages)):
                btn = QPushButton(f"P{p_idx + 1}")
                btn.setCheckable(True)
                btn.setFixedHeight(22)
                btn.setMinimumWidth(34)
                btn.clicked.connect(lambda _checked=False, idx=p_idx: self.jump_to_page(idx))
                self._page_bar_layout.addWidget(btn)
                self._page_buttons.append(btn)

        btn_next = QPushButton("▶")
        btn_next.setFixedSize(26, 22)
        btn_next.setToolTip("Next Page")
        btn_next.clicked.connect(lambda: self._step_page(1))
        self._page_bar_layout.addWidget(btn_next)

        self._page_bar_layout.addStretch()
        hint = QLabel("Right-drag to pan  •  Ctrl+Wheel to zoom")
        hint.setStyleSheet("color: #7c8796; font-size: 11px;")
        self._page_bar_layout.addWidget(hint)

    def _highlight_page_button(self, page_idx: int) -> None:
        for i, btn in enumerate(self._page_buttons):
            btn.setChecked(i == page_idx)
        self.minimap.set_selected_page(page_idx)

    def _step_page(self, delta: int) -> None:
        if not self.project or not self.project.pages:
            return
        cur = self.display_view.canvas.selected_page
        nxt = max(0, min(len(self.project.pages) - 1, cur + delta))
        self.jump_to_page(nxt)

    def jump_to_page(self, page_idx: int) -> None:
        if not self.project or not (0 <= page_idx < len(self.project.pages)):
            return
        page = self.project.pages[page_idx]
        self._highlight_page_button(page_idx)
        self.wiping_view.set_selection(page_idx, 0, center=True)
        self.display_view.set_selection(page_idx, 0, scroll_to=True)
        self._scrub_to(float(page.first_syl_start))

    def _on_minimap_jump(self, cs: float) -> None:
        # Center active view on cs and move playhead there
        self.wiping_view.jump_to_cs(cs)
        self.display_view.jump_to_cs(cs)
        self._scrub_to(cs)
        # Also select the page containing cs if any
        if self.project:
            for p_idx, page in enumerate(self.project.pages):
                if page.display_start <= cs <= page.display_end:
                    self._highlight_page_button(p_idx)
                    self.wiping_view.set_selection(p_idx, 0, center=False)
                    self.display_view.set_selection(p_idx, 0, scroll_to=False)
                    break

    def _sync_minimap_from_active_tab(self) -> None:
        cs = self.audio.smooth_position_cs
        if self._tabs.currentWidget() is self.display_view:
            if self.wiping_view.is_word_adjust_mode:
                self.wiping_view.exit_word_adjust_mode()
            self.display_view.set_playhead_cs(cs, follow_if_playing=False)
            self.minimap.set_viewport(self.display_view.canvas.scroll_x_cs, self.display_view.canvas.view_span_cs())
        else:
            self.wiping_view.set_playhead_cs(cs, follow_if_playing=False)
            self.minimap.set_viewport(self.wiping_view.canvas.scroll_x_cs, self.wiping_view.canvas.view_span_cs())

    def _build_transport_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setStyleSheet("background-color: #1f232a; border-top: 1px solid #353b47;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setFixedWidth(90)
        self._play_btn.clicked.connect(self.audio.toggle_play)
        layout.addWidget(self._play_btn)

        self._play_line_btn = QPushButton("Play Line")
        self._play_line_btn.clicked.connect(self._play_selected_line)
        layout.addWidget(self._play_line_btn)

        self._play_word_btn = QPushButton("Play Word")
        self._play_word_btn.setToolTip("Play the selected word/syllable and stop right at its end")
        self._play_word_btn.clicked.connect(self._play_selected_word)
        layout.addWidget(self._play_word_btn)

        layout.addWidget(QLabel("Speed:"))
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["50%", "75%", "100%"])
        self._speed_combo.setCurrentText("100%")
        self._speed_combo.currentTextChanged.connect(self._on_speed_changed)
        layout.addWidget(self._speed_combo)

        self._audio_src_combo = QComboBox()
        self._audio_src_combo.addItems(["Main Audio", "Vocal Stem"])
        self._audio_src_combo.currentIndexChanged.connect(lambda idx: self.audio.set_use_vocal_audio(idx == 1))
        layout.addWidget(self._audio_src_combo)

        self._load_vocal_btn = QPushButton("Load Vocal Stem…")
        self._load_vocal_btn.clicked.connect(self._open_vocal_stem_dialog)
        layout.addWidget(self._load_vocal_btn)

        self._scrub_audio_cb = QCheckBox("Scrub Audio")
        self._scrub_audio_cb.setChecked(False)
        self._scrub_audio_cb.setToolTip("Hear short audio snippets while dragging the playhead")
        self._scrub_audio_cb.toggled.connect(self.audio.set_scrub_audio_enabled)
        layout.addWidget(self._scrub_audio_cb)

        layout.addSpacing(12)
        btn_zin = QPushButton("Zoom +")
        btn_zin.clicked.connect(lambda: self._zoom_active(1.25))
        layout.addWidget(btn_zin)
        btn_zout = QPushButton("Zoom −")
        btn_zout.clicked.connect(lambda: self._zoom_active(0.8))
        layout.addWidget(btn_zout)
        btn_zreset = QPushButton("100%")
        btn_zreset.setToolTip("Reset Zoom to 100% (Ctrl+0)")
        btn_zreset.clicked.connect(self._reset_zoom_active)
        layout.addWidget(btn_zreset)

        self._preview_btn = QPushButton("16:9 Preview")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setChecked(True)
        self._preview_btn.toggled.connect(self._set_preview_visible)
        layout.addWidget(self._preview_btn)

        layout.addStretch()
        self._time_lbl = QLabel("00:00.00 (0 cs)")
        self._time_lbl.setStyleSheet("font-family: Consolas; font-size: 13px; color: #e2e8f0;")
        layout.addWidget(self._time_lbl)
        return bar

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        act_open = QAction("&Open .kbp…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open_dialog)
        m_file.addAction(act_open)

        self._act_save = QAction("&Save", self)
        self._act_save.setShortcut(QKeySequence.StandardKey.Save)
        self._act_save.triggered.connect(self.save_kbp)
        m_file.addAction(self._act_save)

        self._act_save_as = QAction("Save &As…", self)
        self._act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self._act_save_as.triggered.connect(self.save_kbp_as)
        m_file.addAction(self._act_save_as)

        m_file.addSeparator()
        act_vocal = QAction("Load &Vocal Stem Audio…", self)
        act_vocal.triggered.connect(self._open_vocal_stem_dialog)
        m_file.addAction(act_vocal)

        m_file.addSeparator()
        act_exit = QAction("E&xit", self)
        act_exit.triggered.connect(self.close)
        m_file.addAction(act_exit)

        m_edit = mb.addMenu("&Edit")
        self._act_undo = QAction("&Undo", self)
        self._act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self._act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_undo.triggered.connect(self.undo)
        m_edit.addAction(self._act_undo)

        self._act_redo = QAction("&Redo", self)
        self._act_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self._act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_redo.triggered.connect(self.redo)
        m_edit.addAction(self._act_redo)

        m_edit.addSeparator()
        act_shift_all = QAction("Shift &Whole Track (±cs)…", self)
        act_shift_all.triggered.connect(self._shift_whole_track)
        m_edit.addAction(act_shift_all)

        act_shift_page = QAction("Shift Selected &Page (±cs)…", self)
        act_shift_page.triggered.connect(self._shift_selected_page)
        m_edit.addAction(act_shift_page)

        act_dup_line = QAction("Duplicate Selected &Line ([B]/[C])", self)
        act_dup_line.triggered.connect(self.wiping_view._duplicate_selected_line)
        m_edit.addAction(act_dup_line)

        act_dup_page = QAction("&Duplicate Selected Page ([B]/[C])", self)
        act_dup_page.setShortcut(QKeySequence("Ctrl+D"))
        act_dup_page.triggered.connect(self.wiping_view._duplicate_selected_page)
        m_edit.addAction(act_dup_page)

        m_edit.addSeparator()
        self._act_word_adjust = QAction("&Word Adjust Mode (Enter)", self)
        self._act_word_adjust.setCheckable(True)
        self._act_word_adjust.toggled.connect(self._toggle_word_adjust_from_menu)
        m_edit.addAction(self._act_word_adjust)

        m_win = mb.addMenu("&Windows")
        act_reset_all = QAction("&Reset All Windows to Default", self)
        act_reset_all.triggered.connect(lambda: self.display_view._reset_windows(all_pages=True))
        m_win.addAction(act_reset_all)

        act_opt_all = QAction("&Optimized Windows (All Pages)", self)
        act_opt_all.triggered.connect(lambda: self.display_view._optimize_windows(all_pages=True))
        m_win.addAction(act_opt_all)

        m_view = mb.addMenu("&View")
        self._act_preview = QAction("&16:9 Live Preview", self)
        self._act_preview.setCheckable(True)
        self._act_preview.setChecked(True)
        self._act_preview.setShortcut(QKeySequence("F9"))
        self._act_preview.toggled.connect(self._set_preview_visible)
        m_view.addAction(self._act_preview)

        act_reset_zoom = QAction("&Reset Zoom (100%)", self)
        act_reset_zoom.setShortcut(QKeySequence("Ctrl+0"))
        act_reset_zoom.triggered.connect(self._reset_zoom_active)
        m_view.addAction(act_reset_zoom)

        self.preview_win.visibility_changed.connect(self._on_preview_visibility_changed)

        act_play = QAction("Play / Pause", self)
        act_play.setShortcut(QKeySequence(Qt.Key.Key_Space))
        act_play.triggered.connect(self._on_space_pressed)
        self.addAction(act_play)

        act_clear_sel = QAction("Clear Selection / Exit Word Adjust", self)
        act_clear_sel.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        act_clear_sel.triggered.connect(self._on_escape_pressed)
        self.addAction(act_clear_sel)

    def _wire_signals(self) -> None:
        self.audio.position_changed_float.connect(self._on_playhead_moved)
        self.audio.state_changed.connect(lambda playing: self._play_btn.setText("⏸ Pause" if playing else "▶ Play"))
        self.audio.waveform_loaded.connect(self._on_waveform_loaded)

        self.display_view.playhead_scrubbed.connect(self._scrub_to)
        self.wiping_view.playhead_scrubbed.connect(self._scrub_to)

        self.display_view.viewport_changed.connect(self._on_display_viewport)
        self.wiping_view.viewport_changed.connect(self._on_wiping_viewport)

        self.display_view.selection_changed.connect(self._on_display_selection)
        self.wiping_view.selection_changed.connect(self._on_wiping_selection)

        self.display_view.project_mutated.connect(self._on_project_mutated)
        self.wiping_view.project_mutated.connect(self._on_project_mutated)

        self.display_view.auto_reset_display_changed.connect(self._on_auto_reset_display_changed)
        self.wiping_view.auto_reset_display_changed.connect(self._on_auto_reset_display_changed)

        self.wiping_view.play_line_requested.connect(self.audio.play_range)
        self.wiping_view.play_word_requested.connect(self.audio.play_range)
        self.wiping_view.word_adjust_mode_changed.connect(self._on_word_adjust_mode_changed)

    def _on_auto_reset_display_changed(self, enabled: bool) -> None:
        self._settings.setValue("auto_reset_display", bool(enabled))
        self.wiping_view.set_auto_reset_display(enabled)
        self.display_view.set_auto_reset_display(enabled)
        if enabled and self.project is not None:
            self.project.optimize_windows()
            self._on_project_mutated("Auto Reset Display (with ➣➣➣ Optimization)")

    def _on_waveform_loaded(self) -> None:
        self.wiping_view.canvas.invalidate_wave_cache()
        self.wiping_view.canvas.update()
        self.minimap.invalidate_cache()

    def _on_display_viewport(self, start_cs: float, span_cs: float) -> None:
        if self._tabs.currentWidget() is self.display_view:
            self.minimap.set_viewport(start_cs, span_cs)

    def _on_wiping_viewport(self, start_cs: float, span_cs: float) -> None:
        if self._tabs.currentWidget() is self.wiping_view:
            self.minimap.set_viewport(start_cs, span_cs)

    def _scrub_to(self, cs: float) -> None:
        self.audio.set_position_cs(cs, scrub_audio=True)
        self._on_playhead_moved(cs)

    def _on_playhead_moved(self, cs: float) -> None:
        playing = self.audio.is_playing
        self._time_lbl.setText(_fmt_time(cs))
        if self._tabs.currentWidget() is self.display_view:
            self.display_view.set_playhead_cs(cs, follow_if_playing=playing)
        else:
            self.wiping_view.set_playhead_cs(cs, follow_if_playing=playing)
        if self.preview_win.isVisible():
            self.preview_win.canvas.set_time_cs(cs)
        self.minimap.set_playhead_cs(cs)

    def _on_display_selection(self, page_idx: int, line_idx: int) -> None:
        self._highlight_page_button(page_idx)
        self.wiping_view.set_selection(page_idx, line_idx, center=True)

    def _on_wiping_selection(self, page_idx: int, line_idx: int) -> None:
        self._highlight_page_button(page_idx)
        self.display_view.set_selection(page_idx, line_idx, scroll_to=True)

    def _on_speed_changed(self, text: str) -> None:
        rate = {"50%": 0.5, "75%": 0.75, "100%": 1.0}.get(text, 1.0)
        self.audio.set_playback_rate(rate)

    def _zoom_active(self, factor: float) -> None:
        if self._tabs.currentWidget() is self.display_view:
            self.display_view.zoom(factor)
        else:
            self.wiping_view.zoom(factor)

    def _reset_zoom_active(self) -> None:
        if self._tabs.currentWidget() is self.display_view:
            self.display_view.reset_zoom()
        else:
            self.wiping_view.reset_zoom()

    def _play_selected_line(self) -> None:
        if not self.project or not self.project.pages:
            return
        p_idx = self.wiping_view.canvas.page_idx
        l_idx = self.wiping_view.canvas.line_idx
        if 0 <= p_idx < len(self.project.pages) and 0 <= l_idx < len(self.project.pages[p_idx].lines):
            ln = self.project.pages[p_idx].lines[l_idx]
            self.audio.play_range(max(0, ln.first_syl_start - 15), ln.last_syl_end)

    def _play_selected_word(self) -> None:
        self.wiping_view.play_current_word()

    def _toggle_word_adjust_from_menu(self, checked: bool) -> None:
        if checked:
            if self._tabs.currentWidget() is not self.wiping_view:
                self._tabs.setCurrentWidget(self.wiping_view)
            self.wiping_view.enter_word_adjust_mode()
        else:
            self.wiping_view.exit_word_adjust_mode()

    def _on_word_adjust_mode_changed(self, active: bool) -> None:
        self._act_word_adjust.blockSignals(True)
        self._act_word_adjust.setChecked(active)
        self._act_word_adjust.blockSignals(False)

    def _on_space_pressed(self) -> None:
        if self._tabs.currentWidget() is self.wiping_view and self.wiping_view.is_word_adjust_mode:
            if self.audio.is_playing:
                self.audio.pause()
            else:
                self.wiping_view.play_current_word()
        else:
            self.audio.toggle_play()

    def _on_escape_pressed(self) -> None:
        if self.wiping_view.is_word_adjust_mode:
            self.wiping_view.exit_word_adjust_mode()
        else:
            self.wiping_view.canvas.clear_selection()

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open KBP Project", "", "Karaoke Builder Projects (*.kbp);;All Files (*.*)")
        if path:
            self.open_kbp(path)

    def _open_vocal_stem_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Vocal Stem Audio", "", "Audio Files (*.flac *.wav *.mp3 *.ogg);;All Files (*.*)")
        if path:
            if self.audio.load_vocal_stem(path):
                self.statusBar().showMessage(f"Loaded vocal stem: {Path(path).name}", 4000)

    def open_kbp(self, path: str | Path) -> None:
        try:
            proj = KbpProject.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not load KBP file:\n{exc}")
            return

        self.project = proj
        self.history.reset(proj, label=f"Open {Path(path).name}")

        bak_path: Optional[Path] = None
        if proj.load_issues:
            try:
                bak_path = KbpProject.create_backup(Path(path))
            except Exception:
                bak_path = None
            self.history.mark_dirty()

        self._bind_project_to_views(preserve_view=False)
        self._rebuild_page_buttons()

        if proj.header.audio_path and Path(proj.header.audio_path).exists():
            self.audio.load_main(proj.header.audio_path)

        if proj.pages:
            self._highlight_page_button(0)
            self._scrub_to(float(proj.pages[0].display_start))
        self._sync_minimap_from_active_tab()
        self._refresh_actions()
        self.statusBar().showMessage(f"Opened {Path(path).name} ({len(proj.pages)} pages)", 4000)

        if proj.load_issues:
            self._show_repair_warning(Path(path), proj.load_issues, bak_path)

    def _show_repair_warning(self, orig_path: Path, issues: list, bak_path: Optional[Path]) -> None:
        count = len(issues)
        preview_lines = "\n".join(f"• {iss}" for iss in issues[:6])
        if count > 6:
            preview_lines += f"\n… and {count - 6} more (click 'Show Details…' for the full list)."

        bak_note = (
            f"A backup of the untouched original file was saved to:\n{bak_path.name}"
            if bak_path is not None
            else "The original file on disk has NOT been modified."
        )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Non-Standard KBP File Auto-Repaired")
        msg.setText(
            f"Detected and automatically repaired {count} non-standard timing/format "
            f"issue{'s' if count != 1 else ''} in '{orig_path.name}'."
        )
        msg.setInformativeText(
            f"{preview_lines}\n\n"
            f"Please review and verify the sync at the affected location(s) before saving.\n"
            f"{bak_note}"
        )
        msg.setDetailedText("\n".join(str(iss) for iss in issues))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _bind_project_to_views(self, preserve_view: bool = False) -> None:
        self.display_view.set_project(self.project, preserve_view=preserve_view)
        self.wiping_view.set_project(self.project, preserve_view=preserve_view)
        self.preview_win.canvas.set_project(self.project)
        self.minimap.set_project(self.project)

    def _on_project_mutated(self, label: str) -> None:
        if self.project is None:
            return
        if self.wiping_view.auto_reset_display_enabled and not label.startswith("Reset ") and not label.startswith("Apply Optimized"):
            self.project.optimize_windows()
        self.history.push(label, self.project)
        if len(self._page_buttons) != len(self.project.pages):
            self._rebuild_page_buttons()
            self._highlight_page_button(min(self.display_view.canvas.selected_page, max(0, len(self.project.pages) - 1)))
            self.wiping_view.set_project(self.project, preserve_view=True)
        self.display_view.set_project(self.project, preserve_view=True)
        self.wiping_view.canvas.update()
        self.preview_win.canvas.invalidate_cache()
        self.minimap.invalidate_cache()
        self._refresh_actions()
        self.statusBar().showMessage(f"{label}  (Undo stack: {self.history.current_index})", 3000)

    def _set_preview_visible(self, visible: bool) -> None:
        if visible:
            if not self._preview_positioned:
                g = self.geometry()
                self.preview_win.move(g.right() - self.preview_win.width() - 36, g.bottom() - self.preview_win.height() - 68)
                self._preview_positioned = True
            self.preview_win.canvas.set_time_cs(self.audio.smooth_position_cs)
            self.preview_win.show()
        else:
            self.preview_win.hide()

    def _on_preview_visibility_changed(self, visible: bool) -> None:
        self._act_preview.blockSignals(True)
        self._act_preview.setChecked(visible)
        self._act_preview.blockSignals(False)
        self._preview_btn.blockSignals(True)
        self._preview_btn.setChecked(visible)
        self._preview_btn.blockSignals(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._preview_positioned and self._act_preview.isChecked():
            self._set_preview_visible(True)

    def closeEvent(self, event) -> None:
        self.preview_win.close()
        super().closeEvent(event)

    def undo(self) -> None:
        snap = self.history.undo()
        if snap is not None:
            self.project = snap
            self._bind_project_to_views(preserve_view=True)
            if len(self._page_buttons) != len(self.project.pages):
                self._rebuild_page_buttons()
            self._highlight_page_button(self.display_view.canvas.selected_page)
            self._sync_minimap_from_active_tab()
            self._refresh_actions()
            self.statusBar().showMessage("Undid last action", 2500)

    def redo(self) -> None:
        snap = self.history.redo()
        if snap is not None:
            self.project = snap
            self._bind_project_to_views(preserve_view=True)
            if len(self._page_buttons) != len(self.project.pages):
                self._rebuild_page_buttons()
            self._highlight_page_button(self.display_view.canvas.selected_page)
            self._sync_minimap_from_active_tab()
            self._refresh_actions()
            self.statusBar().showMessage("Redid action", 2500)

    def save_kbp(self) -> None:
        if not self.project:
            return
        if self.project.source_path is None:
            self.save_kbp_as()
            return
        self.project.save()
        self.history.mark_saved()
        self._refresh_actions()
        self.statusBar().showMessage(f"Saved {self.project.source_path.name}", 3500)

    def save_kbp_as(self) -> None:
        if not self.project:
            return
        default_dir = str(self.project.source_path.parent) if self.project.source_path else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save KBP Project As", default_dir, "Karaoke Builder Projects (*.kbp)")
        if path:
            self.project.save(path)
            self.history.mark_saved()
            self._refresh_actions()
            self.statusBar().showMessage(f"Saved {Path(path).name}", 3500)

    def _shift_whole_track(self) -> None:
        if not self.project:
            return
        delta, ok = QInputDialog.getInt(self, "Shift Whole Track", "Offset in centiseconds (±100 = ±1.00s):", 0, -60000, 60000, 10)
        if ok and delta != 0:
            self.project.shift_all(delta)
            self._on_project_mutated(f"Shift Whole Track ({delta:+d} cs)")

    def _shift_selected_page(self) -> None:
        if not self.project or not self.project.pages:
            return
        p_idx = self.display_view.canvas.selected_page
        delta, ok = QInputDialog.getInt(self, f"Shift Page {p_idx + 1}", "Offset in centiseconds (±100 = ±1.00s):", 0, -60000, 60000, 10)
        if ok and delta != 0:
            self.project.shift_page(p_idx, delta)
            self._on_project_mutated(f"Shift Page {p_idx + 1} ({delta:+d} cs)")

    def _refresh_actions(self) -> None:
        has_proj = self.project is not None
        self._act_save.setEnabled(has_proj)
        self._act_save_as.setEnabled(has_proj)

        self._act_undo.setEnabled(self.history.can_undo)
        self._act_undo.setText(f"&Undo: {self.history.undo_label}" if self.history.can_undo else "&Undo")

        self._act_redo.setEnabled(self.history.can_redo)
        self._act_redo.setText(f"&Redo: {self.history.redo_label}" if self.history.can_redo else "&Redo")

        title = "Advanced KBP Editor"
        if self.project and self.project.source_path:
            dirty = "*" if self.history.is_dirty else ""
            title = f"{dirty}{self.project.source_path.name} — Advanced KBP Editor"
        self.setWindowTitle(title)

    @staticmethod
    def _extract_kbp_drop(mime_data) -> Optional[str]:
        if mime_data and mime_data.hasUrls():
            for url in mime_data.urls():
                local = url.toLocalFile()
                if local and local.lower().endswith(".kbp"):
                    return local
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._extract_kbp_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        kbp_path = self._extract_kbp_drop(event.mimeData())
        if kbp_path:
            self.open_kbp(kbp_path)
            event.acceptProposedAction()
        else:
            event.ignore()
