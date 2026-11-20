from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from PySide6.QtCore import Qt, QLineF, QPoint, QRectF, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollBar,
    QSlider,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..audio import AudioEngine, WaveformData
from ..model import KbpProject, Line, Syllable


RULER_H = 26
HANDLE_PX = 7
SNAP_PLAYHEAD_PX = 10.0
DEFAULT_WIPING_PX_PER_CS = 2.4


def _parse_words_to_syllables(raw: str) -> List[str]:
    """
    Parse user-entered text into KBP syllable chunks.
    - Spaces separate words (trailing space attached to the last syllable of each word).
    - '/' splits a word into explicit syllables (e.g. 'to/mor/row land' -> ['to', 'mor', 'row ', 'land']).
    """
    words = [w for w in raw.strip().split(" ") if w]
    out: List[str] = []
    for w_idx, word in enumerate(words):
        parts = [p for p in word.split("/") if p]
        for p_idx, part in enumerate(parts):
            is_last_of_word = (p_idx == len(parts) - 1)
            is_last_word = (w_idx == len(words) - 1)
            out.append(part + (" " if (is_last_of_word and not is_last_word) else ""))
    return out


class AddWordDialog(QDialog):
    """Dialog to insert one or more words/syllables into an already-synced line."""

    def __init__(self, playhead_cs: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Word / Syllable")
        self.setMinimumWidth(380)
        self._playhead_cs = int(round(playhead_cs))

        layout = QVBoxLayout(self)
        hint = QLabel("Enter words (use <b>/</b> to split into syllables, e.g. <code>to/mor/row night</code>):")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("e.g. yeah  or  to/mor/row night")
        self._pos_combo = QComboBox()
        self._pos_combo.addItems([
            "After Selected Syllable",
            "Before Selected Syllable",
            "At Start of Line",
            "At End of Line",
            f"At Playhead ({self._playhead_cs} cs)",
        ])
        self._dur_spin = QSpinBox()
        self._dur_spin.setRange(5, 300)
        self._dur_spin.setValue(30)
        self._dur_spin.setSuffix(" cs / syl")

        form = QFormLayout()
        form.addRow("Word(s) / Syllables:", self._text_edit)
        form.addRow("Insert Position:", self._pos_combo)
        form.addRow("Default Duration:", self._dur_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._text_edit.setFocus()

    @property
    def syllables_to_insert(self) -> List[str]:
        return _parse_words_to_syllables(self._text_edit.text())

    @property
    def position_mode(self) -> int:
        return self._pos_combo.currentIndex()

    @property
    def duration_cs(self) -> int:
        return self._dur_spin.value()


class AddLineDialog(QDialog):
    """Dialog to insert a new synced line onto the current page."""

    def __init__(self, playhead_cs: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Line")
        self.setMinimumWidth(400)
        self._playhead_cs = int(round(playhead_cs))

        layout = QVBoxLayout(self)
        hint = QLabel("Enter line lyrics (use <b>/</b> to split syllables, e.g. <code>to/mor/row night</code>):")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("e.g. Sing a/long to/night")
        self._where_combo = QComboBox()
        self._where_combo.addItems([
            "Right After Selected Line",
            f"At Playhead ({self._playhead_cs} cs)",
        ])
        self._style_combo = QComboBox()
        self._style_combo.addItems(["A", "B", "C"])
        self._dur_spin = QSpinBox()
        self._dur_spin.setRange(5, 300)
        self._dur_spin.setValue(30)
        self._dur_spin.setSuffix(" cs / syl")

        form = QFormLayout()
        form.addRow("Line Lyrics:", self._text_edit)
        form.addRow("Start Time:", self._where_combo)
        form.addRow("Track Style:", self._style_combo)
        form.addRow("Syllable Duration:", self._dur_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._text_edit.setFocus()

    @property
    def syllables(self) -> List[str]:
        return _parse_words_to_syllables(self._text_edit.text())

    @property
    def use_playhead(self) -> bool:
        return self._where_combo.currentIndex() == 1

    @property
    def style_letter(self) -> str:
        return self._style_combo.currentText()

    @property
    def duration_cs(self) -> int:
        return self._dur_spin.value()


class AddPageDialog(QDialog):
    """Dialog to insert a new page with one or more lines of lyrics."""

    def __init__(self, playhead_cs: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Page")
        self.setMinimumWidth(420)
        self._playhead_cs = int(round(playhead_cs))

        layout = QVBoxLayout(self)
        hint = QLabel("Enter one lyric line per row (use <b>/</b> to split syllables):")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._lines_edit = QPlainTextEdit()
        self._lines_edit.setPlaceholderText("Line 1 lyrics here\nLine 2 lyrics here")
        self._lines_edit.setFixedHeight(100)
        layout.addWidget(self._lines_edit)

        self._where_combo = QComboBox()
        self._where_combo.addItems([
            "Right After Current Page",
            f"At Playhead ({self._playhead_cs} cs)",
        ])
        self._style_combo = QComboBox()
        self._style_combo.addItems(["A", "B", "C"])
        self._dur_spin = QSpinBox()
        self._dur_spin.setRange(5, 300)
        self._dur_spin.setValue(30)
        self._dur_spin.setSuffix(" cs / syl")

        form = QFormLayout()
        form.addRow("Start Time:", self._where_combo)
        form.addRow("Track Style:", self._style_combo)
        form.addRow("Syllable Duration:", self._dur_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._lines_edit.setFocus()

    @property
    def lines_syllables(self) -> List[List[str]]:
        out: List[List[str]] = []
        for raw_line in self._lines_edit.toPlainText().splitlines():
            syls = _parse_words_to_syllables(raw_line)
            if syls:
                out.append(syls)
        return out

    @property
    def use_playhead(self) -> bool:
        return self._where_combo.currentIndex() == 1

    @property
    def style_letter(self) -> str:
        return self._style_combo.currentText()

    @property
    def duration_cs(self) -> int:
        return self._dur_spin.value()


class SplitSyllableDialog(QDialog):
    """Interactive dialog letting the user pick the exact text split point and time split point."""

    def __init__(
        self,
        syl: Syllable,
        playhead_cs: float,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Split Syllable")
        self.setMinimumWidth(420)
        self._syl = syl
        self._raw_text = syl.text
        self._stripped = syl.text.rstrip(" ")
        self._trailing = syl.text[len(self._stripped) :]

        layout = QVBoxLayout(self)

        info = QLabel(f"Splitting syllable: <b>{syl.text.strip() or '(space)'}</b>  ({syl.start} – {syl.end} cs)")
        layout.addWidget(info)

        char_len = len(self._stripped)
        self._char_slider = QSlider(Qt.Orientation.Horizontal)
        self._char_slider.setRange(1, max(1, char_len - 1))
        self._char_slider.setValue(max(1, char_len // 2))
        self._char_slider.setEnabled(char_len >= 2)
        self._char_slider.valueChanged.connect(self._on_char_slider)

        self._preview_lbl = QLabel()
        self._preview_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #fbbf24; padding: 4px;")
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._left_edit = QLineEdit()
        self._right_edit = QLineEdit()

        default_time = int(round(playhead_cs)) if (syl.start < playhead_cs < syl.end) else (syl.start + syl.end) // 2
        default_time = max(syl.start + 1, min(syl.end - 1, default_time))

        self._time_slider = QSlider(Qt.Orientation.Horizontal)
        self._time_slider.setRange(syl.start + 1, max(syl.start + 1, syl.end - 1))
        self._time_slider.setValue(default_time)

        self._time_spin = QSpinBox()
        self._time_spin.setRange(syl.start + 1, max(syl.start + 1, syl.end - 1))
        self._time_spin.setValue(default_time)
        self._time_spin.setSuffix(" cs")

        self._time_slider.valueChanged.connect(self._time_spin.setValue)
        self._time_spin.valueChanged.connect(self._time_slider.setValue)

        time_row = QHBoxLayout()
        time_row.addWidget(self._time_slider, 1)
        time_row.addWidget(self._time_spin)
        if syl.start < playhead_cs < syl.end:
            btn_ph = QPushButton("At Playhead")
            btn_ph.clicked.connect(lambda: self._time_spin.setValue(max(syl.start + 1, min(syl.end - 1, int(round(playhead_cs))))))
            time_row.addWidget(btn_ph)

        form = QFormLayout()
        form.addRow("Text Split Position:", self._char_slider)
        form.addRow("Preview:", self._preview_lbl)
        form.addRow("Left Syllable Text:", self._left_edit)
        form.addRow("Right Syllable Text:", self._right_edit)
        form.addRow("Split Time:", time_row)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_char_slider(self._char_slider.value())

    def _on_char_slider(self, cut: int) -> None:
        if len(self._stripped) >= 2:
            left = self._stripped[:cut]
            right = self._stripped[cut:] + self._trailing
        else:
            left = self._stripped
            right = self._trailing or self._stripped
        self._left_edit.setText(left)
        self._right_edit.setText(right)
        self._preview_lbl.setText(f"{left}  |  {right.strip()}")

    @property
    def split_time_cs(self) -> int:
        return self._time_spin.value()

    @property
    def left_text(self) -> str:
        return self._left_edit.text()

    @property
    def right_text(self) -> str:
        return self._right_edit.text()


class _WipingTimelineCanvas(QWidget):
    """
    Continuous full-song waveform + draggable syllable blocks for manual alignment.
    Displays ALL lines across the entire song (auto-stacking overlapping harmony lines into lanes)
    so scrolling or playing anywhere immediately shows and lets you edit every syllable.
    """

    playhead_scrubbed = Signal(float)
    line_clicked = Signal(int, int)          # page_idx, line_idx
    syllable_selected = Signal(int)
    delete_requested = Signal()
    project_mutated = Signal(str)
    viewport_changed = Signal(float, float)  # start_cs, span_cs

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.project: Optional[KbpProject] = None
        self.page_idx: int = 0
        self.line_idx: int = 0
        self.selected_syl: int = 0
        self.selected_syls: Set[Tuple[int, int, int]] = set()
        self._anchor_syl: Optional[Tuple[int, int, int]] = None
        self.playhead_cs: float = 0.0

        self.sticky_borders: bool = True
        self.snap_to_playhead: bool = True
        self.show_display_windows: bool = False
        self._snapped_to_playhead: bool = False
        self.px_per_cs: float = DEFAULT_WIPING_PX_PER_CS
        self.scroll_x_cs: float = 0.0

        # Cached rendered rects for accurate hit testing
        self._rendered_syls: List[Tuple[QRectF, int, int, int]] = []
        self._rendered_line_headers: List[Tuple[QRectF, int, int]] = []
        self._rendered_page_badges: List[Tuple[QRectF, int]] = []
        self._wave_cache: Optional[QPixmap] = None
        self._wave_cache_key: Optional[Tuple] = None

        self._drag_mode: Optional[str] = None  # "scrub", "left", "right", "move", "group_move", "pan"
        self._drag_page_idx: int = 0
        self._drag_line_idx: int = 0
        self._drag_syl_idx: int = -1
        self._drag_offset_cs: int = 0
        self._drag_start_cs: int = 0
        self._drag_orig_syls: Dict[Tuple[int, int, int], Tuple[int, int]] = {}
        self._drag_orig_lines: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self._drag_collapse_target: Optional[Tuple[int, int, int]] = None
        self._drag_label: str = ""
        self._drag_changed: bool = False
        self._drag_sticky_neighbor: Optional[int] = None
        self._pan_start_x: float = 0.0
        self._pan_start_scroll: float = 0.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def invalidate_wave_cache(self) -> None:
        self._wave_cache = None
        self._wave_cache_key = None

    def _flat_syllable_coords(self) -> List[Tuple[int, int, int]]:
        if not self.project:
            return []
        out: List[Tuple[int, int, int]] = []
        for p_i, page in enumerate(self.project.pages):
            for l_i, line in enumerate(page.lines):
                for s_i in range(len(line.syllables)):
                    out.append((p_i, l_i, s_i))
        return out

    def clear_selection(self) -> None:
        if self.selected_syls or self.selected_syl != -1:
            self.selected_syls.clear()
            self.selected_syl = -1
            self._anchor_syl = None
            self.update()

    def select_single(self, p_idx: int, l_idx: int, s_idx: int) -> None:
        self.page_idx = p_idx
        self.line_idx = l_idx
        self.selected_syl = s_idx
        self.selected_syls = {(p_idx, l_idx, s_idx)}
        self._anchor_syl = (p_idx, l_idx, s_idx)
        self.update()

    def select_whole_line(self, p_idx: int, l_idx: int, center: bool = False) -> None:
        line = self.get_line(p_idx, l_idx)
        if not line or not line.syllables:
            return
        self.page_idx = p_idx
        self.line_idx = l_idx
        self.selected_syl = 0
        self.selected_syls = {(p_idx, l_idx, s_i) for s_i in range(len(line.syllables))}
        self._anchor_syl = (p_idx, l_idx, 0)
        if center:
            mid = (line.first_syl_start + line.last_syl_end) * 0.5
            self.center_on_cs(mid)
            return
        self.update()

    def select_whole_page(self, p_idx: int, center: bool = False) -> None:
        if not self.project or not (0 <= p_idx < len(self.project.pages)):
            return
        page = self.project.pages[p_idx]
        coords: Set[Tuple[int, int, int]] = set()
        for l_i, line in enumerate(page.lines):
            for s_i in range(len(line.syllables)):
                coords.add((p_idx, l_i, s_i))
        if not coords:
            return
        self.page_idx = p_idx
        self.line_idx = 0
        self.selected_syl = 0
        self.selected_syls = coords
        self._anchor_syl = (p_idx, 0, 0)
        if center:
            mid = (page.first_syl_start + page.last_syl_end) * 0.5
            self.center_on_cs(mid)
            return
        self.update()

    def _select_range_to(self, target: Tuple[int, int, int]) -> None:
        flat = self._flat_syllable_coords()
        if not flat or target not in flat:
            self.selected_syls = {target}
            return
        anchor = self._anchor_syl if self._anchor_syl in flat else (self.page_idx, self.line_idx, self.selected_syl)
        if anchor not in flat:
            self.selected_syls = {target}
            return
        i0 = flat.index(anchor)
        i1 = flat.index(target)
        lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
        self.selected_syls = set(flat[lo : hi + 1])

    def _begin_group_drag(self, start_cs: int, label: str) -> None:
        self._drag_mode = "group_move"
        self._drag_start_cs = start_cs
        self._drag_label = label
        self._drag_changed = False
        self._drag_orig_syls.clear()
        self._drag_orig_lines.clear()
        if not self.project:
            return
        # Track how many syllables per line are selected so full-line/page drags also shift display windows
        by_line: Dict[Tuple[int, int], int] = {}
        for p_i, l_i, s_i in self.selected_syls:
            line = self.get_line(p_i, l_i)
            if line and 0 <= s_i < len(line.syllables):
                syl = line.syllables[s_i]
                self._drag_orig_syls[(p_i, l_i, s_i)] = (syl.start, syl.end)
                by_line[(p_i, l_i)] = by_line.get((p_i, l_i), 0) + 1
        for (p_i, l_i), cnt in by_line.items():
            line = self.get_line(p_i, l_i)
            if line and cnt == len(line.syllables):
                self._drag_orig_lines[(p_i, l_i)] = (line.display_start, line.display_end)

    def get_line(self, page_idx: int, line_idx: int) -> Optional[Line]:
        if not self.project:
            return None
        if 0 <= page_idx < len(self.project.pages):
            page = self.project.pages[page_idx]
            if 0 <= line_idx < len(page.lines):
                return page.lines[line_idx]
        return None

    def current_line(self) -> Optional[Line]:
        return self.get_line(self.page_idx, self.line_idx)

    def view_span_cs(self) -> float:
        return max(50.0, self.width() / max(1e-6, self.px_per_cs))

    def _emit_viewport(self) -> None:
        self.viewport_changed.emit(self.scroll_x_cs, self.view_span_cs())

    def set_scroll_x_cs(self, cs: float) -> None:
        self.scroll_x_cs = max(0.0, float(cs))
        self._emit_viewport()
        self.update()

    def center_on_cs(self, cs: float) -> None:
        self.set_scroll_x_cs(cs - self.view_span_cs() * 0.5)

    def set_line(self, page_idx: int, line_idx: int, center: bool = True) -> None:
        self.page_idx = page_idx
        self.line_idx = line_idx
        line = self.current_line()
        if line:
            self.selected_syl = min(self.selected_syl, max(0, len(line.syllables) - 1))
            if not any(p == page_idx and l == line_idx for p, l, _ in self.selected_syls):
                self.selected_syls = {(page_idx, line_idx, self.selected_syl)}
                self._anchor_syl = (page_idx, line_idx, self.selected_syl)
            if center:
                mid = (line.first_syl_start + line.last_syl_end) * 0.5
                self.center_on_cs(mid)
                return
        self.update()

    def cs_to_x(self, cs: float) -> float:
        return (cs - self.scroll_x_cs) * self.px_per_cs

    def x_to_cs(self, x: float) -> int:
        return max(0, int(round(self.scroll_x_cs + x / max(1e-6, self.px_per_cs))))

    def x_to_cs_float(self, x: float) -> float:
        return max(0.0, self.scroll_x_cs + x / max(1e-6, self.px_per_cs))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._emit_viewport()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(20, 22, 27))

        if not self.project:
            painter.setPen(QColor(150, 155, 165))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drag & drop a .kbp file anywhere (or File → Open) to begin.")
            return

        wave_top = RULER_H + 4
        wave_h = int((h - RULER_H) * 0.46)
        syl_top = wave_top + wave_h + 10
        syl_h = max(80, h - syl_top - 10)

        self._paint_grid(painter, w, h)
        self._paint_waveform(painter, w, wave_top, wave_h)
        self._paint_all_visible_syllables(painter, w, syl_top, syl_h)
        if self.show_display_windows:
            self._paint_active_display_window(painter, w, h)
        self._paint_ruler(painter, w)
        self._paint_playhead(painter, h)

    def _paint_active_display_window(self, painter: QPainter, w: int, h: int) -> None:
        """Draw thin delineation lines for the active page's display/remove window when Auto Reset Display is off."""
        if not self.project or not (0 <= self.page_idx < len(self.project.pages)):
            return
        page = self.project.pages[self.page_idx]
        if not page.lines:
            return

        def _draw_marker(cs: float, color: QColor, label: str, y_flag: int, width: float = 1.5) -> None:
            x = self.cs_to_x(cs)
            if -60 <= x <= w + 60:
                painter.save()
                pen = QPen(color, width, Qt.PenStyle.DashDotLine)
                painter.setPen(pen)
                painter.drawLine(QLineF(x, float(RULER_H), x, float(h)))
                # Small flag pill at top just below ruler
                flag_rect = QRectF(x + 2.0, float(y_flag), 56.0, 15.0)
                bg = QColor(color)
                bg.setAlpha(45)
                painter.fillRect(flag_rect, bg)
                painter.setPen(QPen(color, 1))
                painter.drawRect(flag_rect)
                painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                painter.drawText(flag_rect, Qt.AlignmentFlag.AlignCenter, label)
                painter.restore()

        p_num = self.page_idx + 1
        _draw_marker(page.display_start, QColor(56, 189, 248), f"P{p_num} DISP", RULER_H + 4)
        _draw_marker(page.display_end, QColor(244, 114, 182), f"P{p_num} REM", RULER_H + 4)

        # If the active line's own remove time differs from the page's remove time, show it too
        cur_line = self.current_line()
        if cur_line is not None and abs(cur_line.display_end - page.display_end) > 5:
            _draw_marker(cur_line.display_end, QColor(251, 146, 60), f"L{self.line_idx + 1} REM", RULER_H + 22, width=1.2)

    def _paint_grid(self, painter: QPainter, w: int, h: int) -> None:
        step_cs = 50 if self.px_per_cs >= 1.2 else (100 if self.px_per_cs >= 0.4 else 500)
        start_cs = int(self.scroll_x_cs // step_cs) * step_cs
        end_cs = self.x_to_cs(w) + step_cs
        painter.setPen(QPen(QColor(38, 42, 50), 1))
        for t in range(start_cs, end_cs + 1, step_cs):
            x = int(self.cs_to_x(t))
            painter.drawLine(x, RULER_H, x, h)

    def _paint_ruler(self, painter: QPainter, w: int) -> None:
        painter.fillRect(0, 0, w, RULER_H, QColor(33, 37, 44))
        painter.setPen(QColor(70, 75, 86))
        painter.drawLine(0, RULER_H - 1, w, RULER_H - 1)
        painter.setFont(QFont("Segoe UI", 8))

        step_cs = 50 if self.px_per_cs >= 1.5 else (100 if self.px_per_cs >= 0.5 else 500)
        start_cs = int(self.scroll_x_cs // step_cs) * step_cs
        end_cs = self.x_to_cs(w) + step_cs
        for t in range(start_cs, end_cs + 1, step_cs):
            x = int(self.cs_to_x(t))
            painter.setPen(QColor(100, 108, 120))
            painter.drawLine(x, RULER_H - 7, x, RULER_H - 1)
            sec = t / 100.0
            lbl = f"{int(sec // 60)}:{sec % 60:05.2f}"
            painter.setPen(QColor(190, 198, 210))
            painter.drawText(x + 3, 0, 70, RULER_H - 4, Qt.AlignmentFlag.AlignVCenter, lbl)

    def _draw_wave_layer(self, painter: QPainter, wf: WaveformData, w: int, top: int, height: int, color: QColor) -> None:
        peaks = wf.peaks
        n = len(peaks)
        if n == 0 or w <= 0 or height <= 0:
            return
        mid_y = top + height * 0.5
        half_h = height * 0.45
        inv_px = 1.0 / max(1e-6, self.px_per_cs)
        scale_b = wf.buckets_per_sec / 100.0

        px_arr = np.arange(0, w, 2, dtype=np.float64)
        i0 = np.clip(((self.scroll_x_cs + px_arr * inv_px) * scale_b).astype(np.int64), 0, n - 1)
        i1 = np.clip(((self.scroll_x_cs + (px_arr + 2.0) * inv_px) * scale_b).astype(np.int64) + 1, i0 + 1, n)

        amps = np.maximum.reduceat(peaks, i0)
        amps[-1] = float(peaks[int(i0[-1]):int(i1[-1])].max())
        small = (i1 - i0) <= 1
        if np.any(small):
            amps[small] = peaks[i0[small]]

        bars = amps * half_h
        y0s = (mid_y - bars).tolist()
        y1s = (mid_y + bars).tolist()
        lines = [QLineF(px, y0, px, y1) for px, y0, y1 in zip(px_arr.tolist(), y0s, y1s)]
        painter.setPen(QPen(color, 1))
        painter.drawLines(lines)

    def _paint_waveform(self, painter: QPainter, w: int, top: int, height: int) -> None:
        if w <= 0 or height <= 0:
            return
        cache_key = (
            w,
            height,
            round(self.scroll_x_cs, 3),
            round(self.px_per_cs, 5),
            id(self.audio.main_waveform),
            id(self.audio.vocal_waveform),
        )
        if self._wave_cache is None or self._wave_cache_key != cache_key:
            pm = QPixmap(w, height)
            pm.fill(QColor(15, 17, 22))
            p = QPainter(pm)
            p.setPen(QColor(45, 50, 60))
            p.drawLine(0, int(height * 0.5), w, int(height * 0.5))
            if self.audio.main_waveform is not None:
                self._draw_wave_layer(p, self.audio.main_waveform, w, 0, height, QColor(85, 130, 195, 165))
            if self.audio.vocal_waveform is not None:
                self._draw_wave_layer(p, self.audio.vocal_waveform, w, 0, height, QColor(65, 225, 200, 210))
            if self.audio.main_waveform is None and self.audio.vocal_waveform is None:
                p.setPen(QColor(120, 128, 140))
                p.drawText(QRectF(0, 0, w, height), Qt.AlignmentFlag.AlignCenter, "No audio waveform loaded (check Audio path in KBP or load a vocal stem)")
            p.end()
            self._wave_cache = pm
            self._wave_cache_key = cache_key

        painter.drawPixmap(0, top, self._wave_cache)

    def _paint_all_visible_syllables(self, painter: QPainter, w: int, top: int, height: int) -> None:
        self._rendered_syls.clear()
        self._rendered_line_headers.clear()
        self._rendered_page_badges.clear()
        if not self.project:
            return

        vis_start = self.scroll_x_cs - 60.0
        vis_end = self.scroll_x_cs + self.view_span_cs() + 60.0

        # Collect all lines whose syllables intersect the visible time window
        visible_lines: List[Tuple[int, int, Line]] = []
        for p_idx, page in enumerate(self.project.pages):
            # Subtle vertical page marker + draggable Page badge (P1, P2, ...) at page start
            if page.lines:
                px = self.cs_to_x(page.first_syl_start)
                if -40 <= px <= w + 40:
                    painter.setPen(QPen(QColor(75, 135, 110, 160), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(int(px), top - 6, int(px), top + height)
                    badge_rect = QRectF(max(2.0, px - 2.0), top - 16.0, 34.0, 15.0)
                    page_all_sel = all(
                        (p_idx, li, si) in self.selected_syls
                        for li, ln in enumerate(page.lines)
                        for si in range(len(ln.syllables))
                    ) and any(ln.syllables for ln in page.lines)
                    badge_bg = QColor(235, 140, 35) if page_all_sel else QColor(42, 68, 58)
                    badge_fg = QColor(15, 18, 24) if page_all_sel else QColor(170, 235, 200)
                    painter.fillRect(badge_rect, badge_bg)
                    painter.setPen(QPen(QColor(110, 190, 150), 1))
                    painter.drawRect(badge_rect)
                    painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                    painter.setPen(badge_fg)
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"P{p_idx + 1}")
                    self._rendered_page_badges.append((badge_rect, p_idx))

            for l_idx, line in enumerate(page.lines):
                if not line.syllables:
                    continue
                if line.last_syl_end >= vis_start and line.first_syl_start <= vis_end:
                    visible_lines.append((p_idx, l_idx, line))

        if not visible_lines:
            painter.setPen(QColor(110, 118, 130))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(0, top, w, height), Qt.AlignmentFlag.AlignCenter, "Instrumental / gap — use the top Minimap or Page buttons to jump to lyrics")
            return

        # Greedy lane packing so overlapping lines (e.g. harmony vocals / duplicated [B]/[C]) stack cleanly
        lane_ends: List[int] = []
        placed: List[Tuple[int, int, int, Line]] = []  # (lane, p_idx, l_idx, line)
        for p_idx, l_idx, line in visible_lines:
            assigned_lane = -1
            for lane_i, end_cs in enumerate(lane_ends):
                if line.first_syl_start >= end_cs - 1:
                    assigned_lane = lane_i
                    lane_ends[lane_i] = line.last_syl_end
                    break
            if assigned_lane == -1:
                assigned_lane = len(lane_ends)
                lane_ends.append(line.last_syl_end)
            placed.append((assigned_lane, p_idx, l_idx, line))

        num_lanes = max(1, len(lane_ends))
        lane_h = min(100.0, (height - 8.0) / num_lanes)
        label_h = 18.0
        block_h = max(34.0, min(64.0, lane_h - label_h - 10.0))

        for lane_i, p_idx, l_idx, line in placed:
            lane_top = top + 4.0 + lane_i * lane_h
            block_y = lane_top + label_h
            is_sel_line = (p_idx == self.page_idx and l_idx == self.line_idx)
            line_all_sel = bool(line.syllables) and all((p_idx, l_idx, si) in self.selected_syls for si in range(len(line.syllables)))

            # Subtle span highlight for the selected line
            if is_sel_line or line_all_sel:
                x_ls = self.cs_to_x(line.first_syl_start)
                x_le = self.cs_to_x(line.last_syl_end)
                painter.fillRect(
                    QRectF(x_ls - 3, lane_top, max(8.0, x_le - x_ls + 6), label_h + block_h + 4),
                    QColor(251, 191, 36, 26) if line_all_sel else QColor(255, 255, 255, 16),
                )

            # Clickable compact line header label above the line's first syllable
            lbl_x = max(4.0, self.cs_to_x(line.first_syl_start))
            lbl_w = max(160.0, self.cs_to_x(line.last_syl_end) - lbl_x + 40.0)
            hdr_rect = QRectF(lbl_x, lane_top, lbl_w, label_h)
            self._rendered_line_headers.append((hdr_rect, p_idx, l_idx))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold if (is_sel_line or line_all_sel) else QFont.Weight.Normal))
            if line_all_sel or is_sel_line:
                painter.setPen(QColor(251, 191, 36))
            elif line.style == "C":
                painter.setPen(QColor(125, 225, 210))
            elif line.style != "A":
                painter.setPen(QColor(210, 175, 245))
            else:
                painter.setPen(QColor(150, 162, 180))
            line_tag = f"P{p_idx + 1} L{l_idx + 1} [{line.style}] {line.full_text.strip()}"
            painter.drawText(hdr_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line_tag)

            # Draw each syllable block
            for s_idx, syl in enumerate(line.syllables):
                x0 = self.cs_to_x(syl.start)
                x1 = self.cs_to_x(syl.end)
                if x1 < -20 or x0 > w + 20:
                    continue
                bw = max(6.0, x1 - x0)

                is_sel = (p_idx, l_idx, s_idx) in self.selected_syls
                is_primary = is_sel and is_sel_line and (s_idx == self.selected_syl)
                if is_sel:
                    fill = QColor(245, 152, 42) if is_primary else QColor(222, 132, 34)
                    border = QColor(255, 255, 255)
                elif is_sel_line:
                    fill = QColor(58, 126, 192)
                    border = QColor(150, 200, 245)
                elif line.style == "C":
                    fill = QColor(42, 126, 118)
                    border = QColor(115, 210, 200)
                elif line.style != "A":
                    fill = QColor(115, 76, 155)
                    border = QColor(180, 145, 220)
                else:
                    fill = QColor(42, 86, 134)
                    border = QColor(95, 138, 185)

                rect = QRectF(x0, block_y, bw, block_h)
                self._rendered_syls.append((rect, p_idx, l_idx, s_idx))

                painter.fillRect(rect, fill)
                painter.setPen(QPen(border, 2 if is_sel else 1))
                painter.drawRect(rect)

                # Edge handles
                painter.fillRect(QRectF(x0, block_y, 3, block_h), QColor(255, 255, 255, 170))
                painter.fillRect(QRectF(x0 + bw - 3, block_y, 3, block_h), QColor(255, 255, 255, 170))

                # Syllable text
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.setPen(QColor(15, 18, 24) if is_sel else QColor(240, 245, 252))
                painter.drawText(rect.adjusted(3, 2, -3, -16), Qt.AlignmentFlag.AlignCenter, syl.text.strip() or "␣")

                if bw >= 42:
                    painter.setFont(QFont("Consolas", 7))
                    painter.drawText(rect.adjusted(2, block_h - 16, -2, -2), Qt.AlignmentFlag.AlignCenter, f"{syl.start}-{syl.end}")

    def _paint_playhead(self, painter: QPainter, h: int) -> None:
        x = self.cs_to_x(self.playhead_cs)
        if -4 <= x <= self.width() + 4:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._snapped_to_playhead:
                painter.setPen(QPen(QColor(255, 220, 60), 2.5))
            else:
                painter.setPen(QPen(QColor(255, 70, 90), 2.0))
            painter.drawLine(QLineF(x, 0.0, x, float(h)))
            painter.restore()

    def _hit_test(self, x: float, y: float) -> Tuple[str, int, int, int]:
        if y < RULER_H:
            return ("scrub", -1, -1, -1)
        for rect, p_idx in reversed(self._rendered_page_badges):
            if rect.contains(x, y):
                return ("page_badge", p_idx, 0, 0)
        for rect, p_idx, l_idx in reversed(self._rendered_line_headers):
            if rect.contains(x, y):
                return ("line_header", p_idx, l_idx, 0)
        for rect, p_idx, l_idx, s_idx in reversed(self._rendered_syls):
            if rect.top() - 4 <= y <= rect.bottom() + 4:
                if abs(x - rect.left()) <= HANDLE_PX:
                    return ("left", p_idx, l_idx, s_idx)
                if abs(x - rect.right()) <= HANDLE_PX:
                    return ("right", p_idx, l_idx, s_idx)
                if rect.left() < x < rect.right():
                    return ("move", p_idx, l_idx, s_idx)
        return ("scrub", -1, -1, -1)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.project:
            return
        pos = event.position()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._drag_mode = "pan"
            self._pan_start_x = pos.x()
            self._pan_start_scroll = self.scroll_x_cs
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        mode, p_idx, l_idx, s_idx = self._hit_test(pos.x(), pos.y())

        # Clicking/dragging a Page badge selects & slides the entire page
        if mode == "page_badge":
            self.select_whole_page(p_idx, center=False)
            self.line_clicked.emit(p_idx, 0)
            self._begin_group_drag(self.x_to_cs(pos.x()), f"Slide Page {p_idx + 1}")
            self._drag_collapse_target = None
            return

        # Clicking/dragging a Line header selects & slides the entire line (supports Ctrl/Shift!)
        if mode == "line_header":
            line = self.get_line(p_idx, l_idx)
            if not line or not line.syllables:
                return
            line_coords = {(p_idx, l_idx, i) for i in range(len(line.syllables))}
            if ctrl:
                if line_coords.issubset(self.selected_syls) and len(self.selected_syls) > len(line_coords):
                    self.selected_syls -= line_coords
                else:
                    self.selected_syls |= line_coords
                self._anchor_syl = (p_idx, l_idx, 0)
            elif shift:
                self._select_range_to((p_idx, l_idx, len(line.syllables) - 1))
                self.selected_syls |= line_coords
            else:
                if not line_coords.issubset(self.selected_syls):
                    self.selected_syls = set(line_coords)
                self._anchor_syl = (p_idx, l_idx, 0)
            self.page_idx = p_idx
            self.line_idx = l_idx
            self.selected_syl = 0
            self.line_clicked.emit(p_idx, l_idx)
            self._begin_group_drag(self.x_to_cs(pos.x()), f"Slide Line (P{p_idx + 1} L{l_idx + 1})")
            self._drag_collapse_target = None
            self.update()
            return

        line = self.get_line(p_idx, l_idx) if p_idx >= 0 else None

        if mode == "scrub" or not line or s_idx < 0:
            self._drag_mode = "scrub"
            cs = self.x_to_cs_float(pos.x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()
            return

        coord = (p_idx, l_idx, s_idx)
        self._drag_collapse_target = None

        if mode == "move" and ctrl:
            if coord in self.selected_syls:
                self.selected_syls.discard(coord)
                # Move primary cursor to another still-selected syllable (or clear if none)
                if self.selected_syls:
                    nxt = next(iter(self.selected_syls))
                    self.page_idx, self.line_idx, self.selected_syl = nxt
                    self._anchor_syl = nxt
                else:
                    self.selected_syl = -1
                    self._anchor_syl = None
            else:
                self.selected_syls.add(coord)
                self.page_idx, self.line_idx, self.selected_syl = coord
                self._anchor_syl = coord
                self.line_clicked.emit(p_idx, l_idx)
                self.syllable_selected.emit(s_idx)
            self.update()
            return

        if (p_idx, l_idx) != (self.page_idx, self.line_idx):
            self.page_idx = p_idx
            self.line_idx = l_idx
            self.line_clicked.emit(p_idx, l_idx)

        self.selected_syl = s_idx
        self.syllable_selected.emit(s_idx)

        if mode == "move":
            if shift:
                self._select_range_to(coord)
                self.update()
                return
            if coord in self.selected_syls and len(self.selected_syls) > 1:
                # Keep multi-selection active so user can drag the entire highlighted group
                self._drag_collapse_target = coord
                self._begin_group_drag(self.x_to_cs(pos.x()), f"Move {len(self.selected_syls)} Syllables")
                self.update()
                return
            self.selected_syls = {coord}
            self._anchor_syl = coord
        else:
            self.selected_syls = {coord}
            self._anchor_syl = coord

        self._drag_mode = mode
        self._drag_page_idx = p_idx
        self._drag_line_idx = l_idx
        self._drag_syl_idx = s_idx
        self._drag_changed = False
        self._drag_sticky_neighbor = None

        syl = line.syllables[s_idx]
        if mode == "move":
            self._drag_offset_cs = self.x_to_cs(pos.x()) - syl.start
        elif mode == "left" and self.sticky_borders and s_idx > 0:
            prev_syl = line.syllables[s_idx - 1]
            if abs(prev_syl.end - syl.start) <= 2:
                self._drag_sticky_neighbor = s_idx - 1
        elif mode == "right" and self.sticky_borders and s_idx + 1 < len(line.syllables):
            next_syl = line.syllables[s_idx + 1]
            if abs(next_syl.start - syl.end) <= 2:
                self._drag_sticky_neighbor = s_idx + 1
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_mode is None:
            mode, _, _, _ = self._hit_test(pos.x(), pos.y())
            if mode in ("left", "right"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif mode in ("move", "line_header", "page_badge"):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._drag_mode == "pan":
            dx = pos.x() - self._pan_start_x
            self.set_scroll_x_cs(self._pan_start_scroll - dx / max(1e-6, self.px_per_cs))
            return

        if self._drag_mode == "scrub":
            cs = self.x_to_cs_float(pos.x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()
            return

        self._snapped_to_playhead = False
        ph_cs = int(round(self.playhead_cs))
        snap_tol_cs = SNAP_PLAYHEAD_PX / max(1e-6, self.px_per_cs)

        if self._drag_mode == "group_move":
            if not self._drag_orig_syls:
                return
            cs = self.x_to_cs(pos.x())
            raw_delta = cs - self._drag_start_cs
            min_start = min(st for st, _ in self._drag_orig_syls.values())
            max_end = max(en for _, en in self._drag_orig_syls.values())
            delta = max(-min_start, raw_delta)
            if self.snap_to_playhead:
                d_left = abs((min_start + delta) - ph_cs)
                d_right = abs((max_end + delta) - ph_cs)
                if d_left <= snap_tol_cs and d_left <= d_right:
                    delta = max(-min_start, ph_cs - min_start)
                    self._snapped_to_playhead = True
                elif d_right <= snap_tol_cs:
                    delta = max(-min_start, ph_cs - max_end)
                    self._snapped_to_playhead = True
            if delta != 0 or self._drag_changed:
                for (p_i, l_i, s_i), (orig_s, orig_e) in self._drag_orig_syls.items():
                    line = self.get_line(p_i, l_i)
                    if line and 0 <= s_i < len(line.syllables):
                        line.syllables[s_i].start = orig_s + delta
                        line.syllables[s_i].end = orig_e + delta
                for (p_i, l_i), (orig_ds, orig_de) in self._drag_orig_lines.items():
                    line = self.get_line(p_i, l_i)
                    if line:
                        line.display_start = max(0, orig_ds + delta)
                        line.display_end = max(line.display_start, orig_de + delta)
                self._drag_changed = (delta != 0)
                self.update()
            return

        cs = self.x_to_cs(pos.x())
        line = self.get_line(self._drag_page_idx, self._drag_line_idx)
        if not line or not (0 <= self._drag_syl_idx < len(line.syllables)):
            return
        syl = line.syllables[self._drag_syl_idx]

        if self._drag_mode == "left":
            min_cs = 0
            if self._drag_sticky_neighbor is not None:
                min_cs = line.syllables[self._drag_sticky_neighbor].start + 1
            elif self._drag_syl_idx > 0:
                min_cs = line.syllables[self._drag_syl_idx - 1].end
            target_cs = cs
            if self.snap_to_playhead and abs(target_cs - ph_cs) <= snap_tol_cs:
                target_cs = ph_cs
                self._snapped_to_playhead = True
            new_start = max(min_cs, min(target_cs, syl.end - 1))
            syl.start = new_start
            if self._drag_sticky_neighbor is not None:
                line.syllables[self._drag_sticky_neighbor].end = new_start
            self._drag_changed = True
        elif self._drag_mode == "right":
            max_cs = 10_000_000
            if self._drag_sticky_neighbor is not None:
                max_cs = line.syllables[self._drag_sticky_neighbor].end - 1
            elif self._drag_syl_idx + 1 < len(line.syllables):
                max_cs = line.syllables[self._drag_syl_idx + 1].start
            target_cs = cs
            if self.snap_to_playhead and abs(target_cs - ph_cs) <= snap_tol_cs:
                target_cs = ph_cs
                self._snapped_to_playhead = True
            new_end = min(max_cs, max(target_cs, syl.start + 1))
            syl.end = new_end
            if self._drag_sticky_neighbor is not None:
                line.syllables[self._drag_sticky_neighbor].start = new_end
            self._drag_changed = True
        elif self._drag_mode == "move":
            dur = syl.end - syl.start
            new_start = max(0, cs - self._drag_offset_cs)
            if self.snap_to_playhead:
                d_left = abs(new_start - ph_cs)
                d_right = abs((new_start + dur) - ph_cs)
                if d_left <= snap_tol_cs and d_left <= d_right:
                    new_start = max(0, ph_cs)
                    self._snapped_to_playhead = True
                elif d_right <= snap_tol_cs:
                    new_start = max(0, ph_cs - dur)
                    self._snapped_to_playhead = True
            syl.start = new_start
            syl.end = new_start + dur
            self._drag_changed = True

        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_snapped = self._snapped_to_playhead
        self._snapped_to_playhead = False
        if self._drag_mode == "pan":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self._drag_mode == "group_move":
            if self._drag_changed:
                self.project_mutated.emit(self._drag_label or "Move Selection")
            elif self._drag_collapse_target is not None:
                # Plain click on an already-selected syllable without dragging collapses selection to it
                self.selected_syls = {self._drag_collapse_target}
                self._anchor_syl = self._drag_collapse_target
                self.update()
        elif self._drag_mode in ("left", "right", "move") and self._drag_changed:
            line = self.get_line(self._drag_page_idx, self._drag_line_idx)
            txt = line.syllables[self._drag_syl_idx].text.strip() if line and 0 <= self._drag_syl_idx < len(line.syllables) else ""
            self.project_mutated.emit(f"Align Syllable '{txt}' (P{self._drag_page_idx + 1} L{self._drag_line_idx + 1})")
        if was_snapped:
            self.update()
        self._drag_mode = None
        self._drag_changed = False
        self._drag_sticky_neighbor = None
        self._drag_collapse_target = None
        self._drag_orig_syls.clear()
        self._drag_orig_lines.clear()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            mode, _, _, _ = self._hit_test(pos.x(), pos.y())
            if mode == "scrub":
                self.clear_selection()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if delta > 0 else 1.0 / 1.25
            mouse_cs = self.x_to_cs_float(event.position().x())
            self.px_per_cs = max(0.2, min(12.0, self.px_per_cs * factor))
            self.set_scroll_x_cs(mouse_cs - event.position().x() / self.px_per_cs)
        else:
            step = -delta / max(0.1, self.px_per_cs) * 0.5
            self.set_scroll_x_cs(self.scroll_x_cs + step)


class LyricsWipingView(QWidget):
    """Manual Alignment ('Lyrics Wiping') tab with page-grouped lyrics panel, waveform, and syllable/line controls."""

    playhead_scrubbed = Signal(float)
    selection_changed = Signal(int, int)
    project_mutated = Signal(str)
    play_line_requested = Signal(int, int)
    viewport_changed = Signal(float, float)

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.project: Optional[KbpProject] = None
        self._item_by_coord: dict[Tuple[int, int], QTreeWidgetItem] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Organized ribbon-style toolbar with titled sections & vertical dividers
        ribbon = QFrame(self)
        ribbon.setStyleSheet(
            "QFrame#wipingRibbon { background-color: #20242c; border: 1px solid #323846; border-radius: 4px; }"
            "QLabel.secTitle { color: #cbd5e1; font-size: 11px; font-weight: bold; padding-bottom: 1px; }"
            "QPushButton { padding: 2px 8px; font-size: 11px; }"
            "QCheckBox { font-size: 11px; }"
        )
        ribbon.setObjectName("wipingRibbon")
        ribbon_layout = QHBoxLayout(ribbon)
        ribbon_layout.setContentsMargins(8, 5, 8, 5)
        ribbon_layout.setSpacing(10)

        def _make_divider() -> QFrame:
            line = QFrame(ribbon)
            line.setFrameShape(QFrame.Shape.VLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet("color: #3a4150;")
            return line

        def _make_section(title: str) -> Tuple[QVBoxLayout, QHBoxLayout, QHBoxLayout]:
            sec = QVBoxLayout()
            sec.setContentsMargins(0, 0, 0, 0)
            sec.setSpacing(3)
            lbl = QLabel(title, ribbon)
            lbl.setProperty("class", "secTitle")
            lbl.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row1 = QHBoxLayout()
            row1.setContentsMargins(0, 0, 0, 0)
            row1.setSpacing(4)
            row2 = QHBoxLayout()
            row2.setContentsMargins(0, 0, 0, 0)
            row2.setSpacing(4)
            sec.addWidget(lbl)
            sec.addLayout(row1)
            sec.addLayout(row2)
            return sec, row1, row2

        def _add_btn(target: QHBoxLayout, label: str, tip: str, fn) -> QPushButton:
            btn = QPushButton(label, ribbon)
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            target.addWidget(btn)
            return btn

        # Section 1: Sync & Display
        sec_sync, sync_r1, sync_r2 = _make_section("Sync & Display")
        self._sticky_cb = QCheckBox("Sticky Borders", ribbon)
        self._sticky_cb.setChecked(True)
        self._sticky_cb.toggled.connect(self._on_sticky)
        sync_r1.addWidget(self._sticky_cb)

        self._follow_cb = QCheckBox("Follow Playhead", ribbon)
        self._follow_cb.setChecked(True)
        sync_r1.addWidget(self._follow_cb)

        self._snap_ph_cb = QCheckBox("Snap to Playhead", ribbon)
        self._snap_ph_cb.setChecked(True)
        self._snap_ph_cb.setToolTip("Magnetically snap syllable/line/page edges to the playhead while dragging")
        self._snap_ph_cb.toggled.connect(self._on_snap_playhead)
        sync_r1.addWidget(self._snap_ph_cb)

        self._auto_reset_cb = QCheckBox("Auto Reset Display", ribbon)
        self._auto_reset_cb.setChecked(True)
        self._auto_reset_cb.setToolTip(
            "When ON: automatically resets display windows & applies ➣➣➣ optimizations on every edit.\n"
            "When OFF: shows the active page's DISP / REM window delineation lines on the timeline."
        )
        self._auto_reset_cb.toggled.connect(self._on_auto_reset_toggled)
        sync_r2.addWidget(self._auto_reset_cb)

        _add_btn(sync_r2, "Reset Displays", "Reset all Display/Remove windows & apply ➣➣➣ optimizations", self._reset_displays)
        ribbon_layout.addLayout(sec_sync)
        ribbon_layout.addWidget(_make_divider())

        # Section 2: Words / Syllables
        sec_words, w_r1, w_r2 = _make_section("Words")
        _add_btn(w_r1, "Add…", "Insert word(s) or syllables into the selected synced line", self._add_word)
        _add_btn(w_r1, "Split…", "Split selected syllable (choose text & time)", self._split_syl)
        _add_btn(w_r1, "Join", "Join selected syllable with next syllable", self._join_syl)
        _add_btn(w_r1, "Delete", "Delete selected syllable(s) (Del)", self._delete_syl)
        _add_btn(w_r2, "Snap Start", "Snap selected syllable's start to previous lyric's end", self._snap_start)
        _add_btn(w_r2, "Snap End", "Snap selected syllable's end to next lyric's start", self._snap_end)
        _add_btn(w_r2, "Snap Both", "Snap both start & end to adjacent lyrics", self._snap_both)
        ribbon_layout.addLayout(sec_words)
        ribbon_layout.addWidget(_make_divider())

        # Section 3: Lines
        sec_lines, l_r1, l_r2 = _make_section("Lines")
        _add_btn(l_r1, "Add…", "Add a new line to the current page", self._add_line)
        _add_btn(l_r1, "Split", "Split current line into two lines at the selected syllable", self._split_line)
        _add_btn(l_r1, "Join", "Join current line with the next line", self._join_line)
        _add_btn(l_r1, "Delete", "Delete the selected line", self._delete_line)
        _add_btn(l_r2, "Select", "Highlight all syllables in the current line to slide it", self._select_current_line)
        _add_btn(l_r2, "Duplicate", "Duplicate selected line onto [B] / [C] track in place", self._duplicate_selected_line)
        _add_btn(l_r2, "▶ Play Line", "Play the selected line and stop right at its end", self._play_current_line)
        ribbon_layout.addLayout(sec_lines)
        ribbon_layout.addWidget(_make_divider())

        # Section 4: Pages
        sec_pages, p_r1, p_r2 = _make_section("Pages")
        _add_btn(p_r1, "Add…", "Add a new page after the current page", self._add_page)
        _add_btn(p_r1, "Split", "Split current page into two pages at the selected line", self._split_page)
        _add_btn(p_r1, "Join", "Merge current page with the next page", self._join_page)
        _add_btn(p_r1, "Delete", "Delete the current page", self._delete_page)
        _add_btn(p_r2, "Select", "Highlight all syllables on the current page to slide it", self._select_current_page)
        _add_btn(p_r2, "Duplicate", "Duplicate current page in-line on [B] / [C] track so you can drag it", self._duplicate_selected_page)
        ribbon_layout.addLayout(sec_pages)
        ribbon_layout.addStretch()

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Clean visual Page-grouped lyrics tree (each Page is its own section; each child is a lyric line)
        self._line_tree = QTreeWidget(splitter)
        self._line_tree.setHeaderHidden(True)
        self._line_tree.setRootIsDecorated(False)
        self._line_tree.setIndentation(10)
        self._line_tree.setMinimumWidth(220)
        self._line_tree.setMaximumWidth(360)
        self._line_tree.setStyleSheet(
            "QTreeWidget { background-color: #191c22; border: 1px solid #2e3440; font-size: 12px; }"
            "QTreeWidget::item { padding: 3px 4px; }"
            "QTreeWidget::item:selected { background-color: #2b5b84; color: #ffffff; }"
        )
        self._line_tree.currentItemChanged.connect(self._on_tree_item_changed)
        self._line_tree.itemClicked.connect(self._on_tree_item_clicked)
        self._line_tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        self._line_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._line_tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        right_box = QWidget(splitter)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        self.canvas = _WipingTimelineCanvas(audio, right_box)
        self.canvas.playhead_scrubbed.connect(self.playhead_scrubbed.emit)
        self.canvas.line_clicked.connect(self._on_canvas_line_clicked)
        self.canvas.delete_requested.connect(self._delete_syl)
        self.canvas.project_mutated.connect(self.project_mutated.emit)
        self.canvas.viewport_changed.connect(self._on_canvas_viewport)

        self._hscroll = QScrollBar(Qt.Orientation.Horizontal, right_box)
        self._hscroll.valueChanged.connect(self._on_hscroll)

        right_layout.addWidget(self.canvas, 1)
        right_layout.addWidget(self._hscroll)

        splitter.addWidget(self._line_tree)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(1, 1)

        root.addWidget(ribbon)
        root.addWidget(splitter, 1)

    @property
    def auto_reset_display_enabled(self) -> bool:
        return self._auto_reset_cb.isChecked()

    def _on_auto_reset_toggled(self, checked: bool) -> None:
        self.canvas.show_display_windows = not checked
        if checked and self.project:
            self.project.optimize_windows()
            self.project_mutated.emit("Auto Reset Display (with ➣➣➣ Optimization)")
        self.canvas.update()

    def set_project(self, project: Optional[KbpProject], preserve_view: bool = False) -> None:
        old_page = self.canvas.page_idx
        old_line = self.canvas.line_idx
        old_syl = self.canvas.selected_syl
        old_scroll = self.canvas.scroll_x_cs

        self.project = project
        self.canvas.project = project
        self._rebuild_tree(preserve_view=preserve_view, target_page=old_page, target_line=old_line)

        if project:
            max_cs = max(1000, project.total_duration_cs + 500)
            self._hscroll.blockSignals(True)
            self._hscroll.setRange(0, max_cs)
            self._hscroll.setPageStep(int(self.canvas.view_span_cs()))
            if preserve_view:
                self.canvas.selected_syl = old_syl
                self.canvas.set_scroll_x_cs(old_scroll)
                self._hscroll.setValue(int(old_scroll))
            self._hscroll.blockSignals(False)
        self.canvas.update()

    def _rebuild_tree(self, preserve_view: bool = False, target_page: int = 0, target_line: int = 0) -> None:
        self._line_tree.blockSignals(True)
        self._line_tree.clear()
        self._item_by_coord.clear()

        header_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        if self.project:
            for p_idx, page in enumerate(self.project.pages):
                is_opt = self.project.is_optimized_lead_in_page(p_idx)
                hdr_label = f"Page {p_idx + 1}"
                page_item = QTreeWidgetItem([hdr_label])
                page_item.setFont(0, header_font)
                page_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # section header, not selectable as a line
                page_item.setBackground(0, QBrush(QColor(34, 58, 48) if is_opt else QColor(34, 40, 52)))
                page_item.setForeground(0, QBrush(QColor(135, 225, 175) if is_opt else QColor(155, 185, 225)))
                if is_opt:
                    page_item.setToolTip(0, "Lead-in page (starts with ➣➣➣ after ≥3.0s instrumental gap)")
                page_item.setData(0, Qt.ItemDataRole.UserRole, (p_idx, -1))
                self._line_tree.addTopLevelItem(page_item)

                for l_idx, line in enumerate(page.lines):
                    text = line.full_text.strip() or "(empty)"
                    child = QTreeWidgetItem([text])
                    child.setData(0, Qt.ItemDataRole.UserRole, (p_idx, l_idx))
                    if line.style != "A":
                        child.setForeground(0, QBrush(QColor(210, 175, 245)))
                    page_item.addChild(child)
                    self._item_by_coord[(p_idx, l_idx)] = child

                page_item.setExpanded(True)

        self._line_tree.blockSignals(False)
        if not self._item_by_coord:
            return
        if preserve_view:
            coord = (target_page, target_line)
            if coord not in self._item_by_coord:
                # Pick nearest valid line
                coords = list(self._item_by_coord.keys())
                coord = coords[min(len(coords) - 1, max(0, target_page))]
            item = self._item_by_coord[coord]
            self._line_tree.blockSignals(True)
            self._line_tree.setCurrentItem(item)
            self._line_tree.blockSignals(False)
            self.canvas.set_line(coord[0], coord[1], center=False)
        else:
            self.set_selection(0, 0)

    def _on_canvas_line_clicked(self, page_idx: int, line_idx: int) -> None:
        item = self._item_by_coord.get((page_idx, line_idx))
        if item is not None:
            self._line_tree.blockSignals(True)
            self._line_tree.setCurrentItem(item)
            self._line_tree.scrollToItem(item)
            self._line_tree.blockSignals(False)
        self.selection_changed.emit(page_idx, line_idx)

    def set_selection(self, page_idx: int, line_idx: int, center: bool = True) -> None:
        item = self._item_by_coord.get((page_idx, line_idx))
        if item is not None:
            self._line_tree.blockSignals(True)
            self._line_tree.setCurrentItem(item)
            self._line_tree.scrollToItem(item)
            self._line_tree.blockSignals(False)
        self.canvas.set_line(page_idx, line_idx, center=center)

    def set_playhead_cs(self, cs: float, follow_if_playing: bool = False) -> None:
        self.canvas.playhead_cs = cs
        if follow_if_playing and self._follow_cb.isChecked():
            span = self.canvas.view_span_cs()
            if cs > self.canvas.scroll_x_cs + span * 0.82 or cs < self.canvas.scroll_x_cs:
                self.canvas.set_scroll_x_cs(cs - span * 0.2)
                return
        self.canvas.update()

    def jump_to_cs(self, cs: float) -> None:
        self.canvas.center_on_cs(cs)

    def zoom(self, factor: float) -> None:
        center_cs = self.canvas.scroll_x_cs + self.canvas.view_span_cs() * 0.5
        self.canvas.px_per_cs = max(0.2, min(12.0, self.canvas.px_per_cs * factor))
        self.canvas.center_on_cs(center_cs)

    def reset_zoom(self) -> None:
        center_cs = self.canvas.scroll_x_cs + self.canvas.view_span_cs() * 0.5
        self.canvas.px_per_cs = DEFAULT_WIPING_PX_PER_CS
        self.canvas.center_on_cs(center_cs)

    def _on_canvas_viewport(self, start_cs: float, span_cs: float) -> None:
        self._hscroll.blockSignals(True)
        self._hscroll.setPageStep(max(50, int(span_cs)))
        self._hscroll.setValue(int(start_cs))
        self._hscroll.blockSignals(False)
        self.viewport_changed.emit(start_cs, span_cs)

    def _on_hscroll(self, value: int) -> None:
        self.canvas.set_scroll_x_cs(float(value))

    def _on_tree_item_changed(self, current: Optional[QTreeWidgetItem], _prev: Optional[QTreeWidgetItem]) -> None:
        if current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        p_idx, l_idx = data
        if l_idx < 0:
            return
        self.canvas.set_line(p_idx, l_idx, center=True)
        self.selection_changed.emit(p_idx, l_idx)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        p_idx, l_idx = data
        if l_idx < 0:
            # Clicking a Page header selects the entire page for sliding!
            self.canvas.select_whole_page(p_idx, center=True)
            self.selection_changed.emit(p_idx, 0)

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[1] >= 0:
            self._play_current_line()

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        item = self._line_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        p_idx, l_idx = data
        menu = QMenu(self)

        if l_idx < 0:
            act_sel_page = QAction(f"Select Whole Page {p_idx + 1} (for sliding)", self)
            act_sel_page.triggered.connect(lambda: self.canvas.select_whole_page(p_idx, center=True))
            menu.addAction(act_sel_page)

            act_dup_page = QAction(f"Duplicate Page {p_idx + 1} → [B]/[C]", self)
            act_dup_page.triggered.connect(lambda: self._duplicate_page_idx(p_idx))
            menu.addAction(act_dup_page)

            menu.addSeparator()
            act_reset_p = QAction(f"Reset Page {p_idx + 1} Display Windows", self)
            act_reset_p.triggered.connect(lambda: self._reset_page_displays(p_idx))
            menu.addAction(act_reset_p)

            menu.exec(self._line_tree.viewport().mapToGlobal(pos))
            return

        self.set_selection(p_idx, l_idx, center=False)

        act_play = QAction("Play Line", self)
        act_play.triggered.connect(self._play_current_line)
        menu.addAction(act_play)

        act_sel_line = QAction("Select Whole Line (for sliding)", self)
        act_sel_line.triggered.connect(lambda: self.canvas.select_whole_line(p_idx, l_idx, center=False))
        menu.addAction(act_sel_line)

        menu.addSeparator()
        act_add_w = QAction("Add Word / Syllable…", self)
        act_add_w.triggered.connect(self._add_word)
        menu.addAction(act_add_w)

        act_split = QAction("Split Line at Selected Syllable", self)
        act_split.triggered.connect(self._split_line)
        menu.addAction(act_split)

        act_join = QAction("Join with Next Line", self)
        act_join.triggered.connect(self._join_line)
        menu.addAction(act_join)

        act_dup_l = QAction("Duplicate Line → [B]/[C]", self)
        act_dup_l.triggered.connect(self._duplicate_selected_line)
        menu.addAction(act_dup_l)

        act_dup_p = QAction(f"Duplicate Page {p_idx + 1} → [B]/[C]", self)
        act_dup_p.triggered.connect(lambda: self._duplicate_page_idx(p_idx))
        menu.addAction(act_dup_p)

        menu.addSeparator()
        act_reset = QAction("Reset All Display Windows", self)
        act_reset.triggered.connect(self._reset_displays)
        menu.addAction(act_reset)

        menu.exec(self._line_tree.viewport().mapToGlobal(pos))

    def _on_sticky(self, checked: bool) -> None:
        self.canvas.sticky_borders = checked

    def _on_snap_playhead(self, checked: bool) -> None:
        self.canvas.snap_to_playhead = checked

    def _snap_start(self) -> None:
        if not self.project:
            return
        if self.project.snap_syllable_start(self.canvas.page_idx, self.canvas.line_idx, self.canvas.selected_syl):
            self.canvas.update()
            self.project_mutated.emit(f"Snap Start to Previous Lyric (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _snap_end(self) -> None:
        if not self.project:
            return
        if self.project.snap_syllable_end(self.canvas.page_idx, self.canvas.line_idx, self.canvas.selected_syl):
            self.canvas.update()
            self.project_mutated.emit(f"Snap End to Next Lyric (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _snap_both(self) -> None:
        if not self.project:
            return
        if self.project.snap_syllable_both(self.canvas.page_idx, self.canvas.line_idx, self.canvas.selected_syl):
            self.canvas.update()
            self.project_mutated.emit(f"Snap Both Edges to Adjacent Lyrics (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _split_syl(self) -> None:
        line = self.canvas.current_line()
        if not line:
            return
        idx = self.canvas.selected_syl
        if not (0 <= idx < len(line.syllables)):
            return
        syl = line.syllables[idx]
        if syl.end - syl.start < 2:
            return
        dlg = SplitSyllableDialog(syl, self.canvas.playhead_cs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if line.split_syllable(
                idx,
                at_cs=dlg.split_time_cs,
                left_text=dlg.left_text,
                right_text=dlg.right_text,
            ):
                self._rebuild_tree(preserve_view=True, target_page=self.canvas.page_idx, target_line=self.canvas.line_idx)
                self.canvas.update()
                self.project_mutated.emit(f"Split Syllable '{syl.text.strip()}' (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _join_syl(self) -> None:
        line = self.canvas.current_line()
        if not line:
            return
        if line.join_syllables(self.canvas.selected_syl):
            self._rebuild_tree(preserve_view=True, target_page=self.canvas.page_idx, target_line=self.canvas.line_idx)
            self.canvas.update()
            self.project_mutated.emit(f"Join Syllables (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _add_word(self) -> None:
        line = self.canvas.current_line()
        if not line:
            return
        dlg = AddWordDialog(self.canvas.playhead_cs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        syls = dlg.syllables_to_insert
        if not syls:
            return
        mode = dlg.position_mode
        start_cs: Optional[int] = None
        if mode == 0:  # After selected
            ins_idx = min(len(line.syllables), self.canvas.selected_syl + 1)
        elif mode == 1:  # Before selected
            ins_idx = max(0, self.canvas.selected_syl)
        elif mode == 2:  # Start of line
            ins_idx = 0
        elif mode == 3:  # End of line
            ins_idx = len(line.syllables)
        else:  # At playhead
            start_cs = int(round(self.canvas.playhead_cs))
            ins_idx = len(line.syllables)
            for i, s in enumerate(line.syllables):
                if s.start >= start_cs:
                    ins_idx = i
                    break

        if line.insert_syllables(ins_idx, syls, start_cs=start_cs, duration_cs=dlg.duration_cs):
            self.canvas.select_single(self.canvas.page_idx, self.canvas.line_idx, ins_idx)
            self._rebuild_tree(preserve_view=True, target_page=self.canvas.page_idx, target_line=self.canvas.line_idx)
            self.canvas.update()
            self.project_mutated.emit(f"Add Word '{''.join(syls).strip()}' (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

    def _delete_syl(self) -> None:
        if not self.project:
            return
        # Support deleting all multi-selected syllables across one or more lines
        by_line: Dict[Tuple[int, int], List[int]] = {}
        if self.canvas.selected_syls:
            for p_i, l_i, s_i in self.canvas.selected_syls:
                by_line.setdefault((p_i, l_i), []).append(s_i)
        else:
            by_line[(self.canvas.page_idx, self.canvas.line_idx)] = [self.canvas.selected_syl]

        changed = False
        total_deleted = 0
        for (p_i, l_i), indices in by_line.items():
            line = self.canvas.get_line(p_i, l_i)
            if not line:
                continue
            before = len(line.syllables)
            if line.delete_syllables(indices):
                changed = True
                total_deleted += before - len(line.syllables)

        if changed:
            cur_line = self.canvas.current_line()
            new_s = max(0, min(self.canvas.selected_syl, len(cur_line.syllables) - 1)) if cur_line else 0
            self.canvas.select_single(self.canvas.page_idx, self.canvas.line_idx, new_s)
            self._rebuild_tree(preserve_view=True, target_page=self.canvas.page_idx, target_line=self.canvas.line_idx)
            self.canvas.update()
            lbl = f"Delete {total_deleted} Syllables" if total_deleted > 1 else f"Delete Syllable (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})"
            self.project_mutated.emit(lbl)

    def _select_current_line(self) -> None:
        self.canvas.select_whole_line(self.canvas.page_idx, self.canvas.line_idx, center=False)

    def _select_current_page(self) -> None:
        self.canvas.select_whole_page(self.canvas.page_idx, center=False)

    def _duplicate_selected_line(self) -> None:
        if not self.project:
            return
        p_idx, l_idx = self.canvas.page_idx, self.canvas.line_idx
        new_line = self.project.duplicate_line(p_idx, l_idx)
        if new_line is not None:
            new_l_idx = l_idx + 1
            self._rebuild_tree(preserve_view=True, target_page=p_idx, target_line=new_l_idx)
            self.canvas.select_whole_line(p_idx, new_l_idx, center=False)
            self.project_mutated.emit(f"Duplicate Line (P{p_idx + 1} L{l_idx + 1}) → [{new_line.style}]")

    def _duplicate_selected_page(self) -> None:
        self._duplicate_page_idx(self.canvas.page_idx)

    def _duplicate_page_idx(self, p_idx: int) -> None:
        if not self.project or not (0 <= p_idx < len(self.project.pages)):
            return
        target_style = self.project.pick_duplicate_style(self.project.pages[p_idx].lines)
        self.project.duplicate_page(page_number=p_idx + 1, target_style=target_style, in_place=True)
        new_p_idx = p_idx + 1
        self._rebuild_tree(preserve_view=True, target_page=new_p_idx, target_line=0)
        self.canvas.select_whole_page(new_p_idx, center=False)
        self.project_mutated.emit(f"Duplicate Page {p_idx + 1} → [{target_style}] (in place)")

    def _add_line(self) -> None:
        if not self.project or not self.project.pages:
            return
        dlg = AddLineDialog(self.canvas.playhead_cs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        syls = dlg.syllables
        if not syls:
            return
        p_idx, l_idx = self.canvas.page_idx, self.canvas.line_idx
        start_cs = int(round(self.canvas.playhead_cs)) if dlg.use_playhead else None
        if self.project.add_line(
            p_idx,
            l_idx,
            syls,
            start_cs=start_cs,
            duration_cs=dlg.duration_cs,
            style=dlg.style_letter,
        ):
            new_l = l_idx + 1
            self._rebuild_tree(preserve_view=True, target_page=p_idx, target_line=new_l)
            self.canvas.select_whole_line(p_idx, new_l, center=False)
            self.project_mutated.emit(f"Add Line (P{p_idx + 1} L{new_l + 1})")

    def _delete_line(self) -> None:
        if not self.project:
            return
        p_idx, l_idx = self.canvas.page_idx, self.canvas.line_idx
        if self.project.delete_line(p_idx, l_idx):
            new_p = min(p_idx, max(0, len(self.project.pages) - 1))
            new_l = min(l_idx, max(0, len(self.project.pages[new_p].lines) - 1))
            self._rebuild_tree(preserve_view=True, target_page=new_p, target_line=new_l)
            self.canvas.update()
            self.project_mutated.emit(f"Delete Line (P{p_idx + 1} L{l_idx + 1})")

    def _add_page(self) -> None:
        if not self.project:
            return
        dlg = AddPageDialog(self.canvas.playhead_cs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        p_idx = self.canvas.page_idx
        start_cs = int(round(self.canvas.playhead_cs)) if dlg.use_playhead else None
        self.project.add_page(
            p_idx,
            dlg.lines_syllables,
            start_cs=start_cs,
            duration_cs=dlg.duration_cs,
            style=dlg.style_letter,
        )
        new_p = min(p_idx + 1, len(self.project.pages) - 1)
        self._rebuild_tree(preserve_view=True, target_page=new_p, target_line=0)
        self.canvas.select_whole_page(new_p, center=True)
        self.project_mutated.emit(f"Add Page {new_p + 1}")

    def _split_page(self) -> None:
        if not self.project:
            return
        p_idx, l_idx = self.canvas.page_idx, self.canvas.line_idx
        if self.project.split_page(p_idx, l_idx):
            self._rebuild_tree(preserve_view=True, target_page=p_idx + 1, target_line=0)
            self.canvas.update()
            self.project_mutated.emit(f"Split Page {p_idx + 1}")

    def _join_page(self) -> None:
        if not self.project:
            return
        p_idx = self.canvas.page_idx
        if self.project.join_pages(p_idx):
            self._rebuild_tree(preserve_view=True, target_page=p_idx, target_line=0)
            self.canvas.update()
            self.project_mutated.emit(f"Join Page {p_idx + 1} & {p_idx + 2}")

    def _delete_page(self) -> None:
        if not self.project:
            return
        p_idx = self.canvas.page_idx
        if self.project.delete_page(p_idx):
            new_p = min(p_idx, max(0, len(self.project.pages) - 1))
            self._rebuild_tree(preserve_view=True, target_page=new_p, target_line=0)
            self.canvas.update()
            self.project_mutated.emit(f"Delete Page {p_idx + 1}")

    def _reset_page_displays(self, p_idx: int) -> None:
        if not self.project:
            return
        self.project.optimize_windows(page_indices=[p_idx])
        self.canvas.update()
        self.project_mutated.emit(f"Reset Page {p_idx + 1} Display Windows")

    def _split_line(self) -> None:
        if not self.project:
            return
        p_idx, l_idx, s_idx = self.canvas.page_idx, self.canvas.line_idx, self.canvas.selected_syl
        if self.project.split_line(p_idx, l_idx, s_idx):
            self.project.reset_windows(page_indices=[p_idx])
            self._rebuild_tree(preserve_view=True, target_page=p_idx, target_line=l_idx)
            self.canvas.update()
            self.project_mutated.emit(f"Split Line (P{p_idx + 1} L{l_idx + 1})")

    def _join_line(self) -> None:
        if not self.project:
            return
        p_idx, l_idx = self.canvas.page_idx, self.canvas.line_idx
        if self.project.join_lines(p_idx, l_idx):
            self.project.reset_windows()
            self._rebuild_tree(preserve_view=True, target_page=p_idx, target_line=l_idx)
            self.canvas.update()
            self.project_mutated.emit(f"Join Line (P{p_idx + 1} L{l_idx + 1})")

    def _reset_displays(self) -> None:
        if not self.project:
            return
        self.project.optimize_windows()
        self.canvas.update()
        self.project_mutated.emit("Reset All Display Windows (with ➣➣➣ Optimization)")

    def _play_current_line(self) -> None:
        line = self.canvas.current_line()
        if line:
            self.play_line_requested.emit(max(0, line.first_syl_start - 15), line.last_syl_end)
