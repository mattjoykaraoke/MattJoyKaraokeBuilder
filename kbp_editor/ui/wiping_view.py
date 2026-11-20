from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QPoint, QRectF, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
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
    project_mutated = Signal(str)
    viewport_changed = Signal(float, float)  # start_cs, span_cs

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.project: Optional[KbpProject] = None
        self.page_idx: int = 0
        self.line_idx: int = 0
        self.selected_syl: int = 0
        self.playhead_cs: float = 0.0

        self.sticky_borders: bool = True
        self.px_per_cs: float = 2.4
        self.scroll_x_cs: float = 0.0

        # Cached rendered syllable rects for accurate hit testing:
        # [(QRectF, page_idx, line_idx, syl_idx)]
        self._rendered_syls: List[Tuple[QRectF, int, int, int]] = []

        self._drag_mode: Optional[str] = None  # "scrub", "left", "right", "move", "pan"
        self._drag_page_idx: int = 0
        self._drag_line_idx: int = 0
        self._drag_syl_idx: int = -1
        self._drag_offset_cs: int = 0
        self._drag_changed: bool = False
        self._drag_sticky_neighbor: Optional[int] = None
        self._pan_start_x: float = 0.0
        self._pan_start_scroll: float = 0.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
        self._paint_ruler(painter, w)
        self._paint_playhead(painter, h)

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
        mid_y = top + height * 0.5
        half_h = height * 0.45
        bps = wf.buckets_per_sec
        peaks = wf.peaks
        n = len(peaks)
        painter.setPen(QPen(color, 1))
        for px in range(0, w, 2):
            cs0 = self.scroll_x_cs + px / max(1e-6, self.px_per_cs)
            cs1 = self.scroll_x_cs + (px + 2) / max(1e-6, self.px_per_cs)
            i0 = max(0, min(n - 1, int(cs0 * bps / 100.0)))
            i1 = max(i0 + 1, min(n, int(cs1 * bps / 100.0) + 1))
            amp = float(peaks[i0:i1].max()) if i0 < n else 0.0
            bar = amp * half_h
            painter.drawLine(px, int(mid_y - bar), px, int(mid_y + bar))

    def _paint_waveform(self, painter: QPainter, w: int, top: int, height: int) -> None:
        painter.fillRect(0, top, w, height, QColor(15, 17, 22))
        painter.setPen(QColor(45, 50, 60))
        painter.drawLine(0, int(top + height * 0.5), w, int(top + height * 0.5))

        if self.audio.main_waveform is not None:
            self._draw_wave_layer(painter, self.audio.main_waveform, w, top, height, QColor(85, 130, 195, 165))
        if self.audio.vocal_waveform is not None:
            self._draw_wave_layer(painter, self.audio.vocal_waveform, w, top, height, QColor(65, 225, 200, 210))

        if self.audio.main_waveform is None and self.audio.vocal_waveform is None:
            painter.setPen(QColor(120, 128, 140))
            painter.drawText(QRectF(0, top, w, height), Qt.AlignmentFlag.AlignCenter, "No audio waveform loaded (check Audio path in KBP or load a vocal stem)")

    def _paint_all_visible_syllables(self, painter: QPainter, w: int, top: int, height: int) -> None:
        self._rendered_syls.clear()
        if not self.project:
            return

        vis_start = self.scroll_x_cs - 60.0
        vis_end = self.scroll_x_cs + self.view_span_cs() + 60.0

        # Collect all lines whose syllables intersect the visible time window
        visible_lines: List[Tuple[int, int, Line]] = []
        for p_idx, page in enumerate(self.project.pages):
            # Subtle vertical page marker at page start
            if page.lines:
                px = self.cs_to_x(page.first_syl_start)
                if -20 <= px <= w + 20:
                    painter.setPen(QPen(QColor(75, 135, 110, 160), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(int(px), top - 6, int(px), top + height)

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

        # Greedy lane packing so overlapping lines (e.g. harmony vocals) stack cleanly
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

            # Subtle span highlight for the selected line
            if is_sel_line:
                x_ls = self.cs_to_x(line.first_syl_start)
                x_le = self.cs_to_x(line.last_syl_end)
                painter.fillRect(QRectF(x_ls - 3, lane_top, max(8.0, x_le - x_ls + 6), label_h + block_h + 4), QColor(255, 255, 255, 16))

            # Compact line header label above the line's first syllable
            lbl_x = max(4.0, self.cs_to_x(line.first_syl_start))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold if is_sel_line else QFont.Weight.Normal))
            painter.setPen(QColor(251, 191, 36) if is_sel_line else QColor(150, 162, 180))
            line_tag = f"P{p_idx + 1} L{l_idx + 1} [{line.style}] {line.full_text.strip()}"
            painter.drawText(QRectF(lbl_x, lane_top, max(160.0, self.cs_to_x(line.last_syl_end) - lbl_x + 40.0), label_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line_tag)

            # Draw each syllable block
            for s_idx, syl in enumerate(line.syllables):
                x0 = self.cs_to_x(syl.start)
                x1 = self.cs_to_x(syl.end)
                if x1 < -20 or x0 > w + 20:
                    continue
                bw = max(6.0, x1 - x0)

                is_sel = is_sel_line and (s_idx == self.selected_syl)
                if is_sel:
                    fill = QColor(245, 152, 42)
                    border = QColor(255, 255, 255)
                elif is_sel_line:
                    fill = QColor(58, 126, 192)
                    border = QColor(150, 200, 245)
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
            painter.setPen(QPen(QColor(255, 70, 90), 2))
            painter.drawLine(int(round(x)), 0, int(round(x)), h)

    def _hit_test(self, x: float, y: float) -> Tuple[str, int, int, int]:
        if y < RULER_H:
            return ("scrub", -1, -1, -1)
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

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._drag_mode = "pan"
            self._pan_start_x = pos.x()
            self._pan_start_scroll = self.scroll_x_cs
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        mode, p_idx, l_idx, s_idx = self._hit_test(pos.x(), pos.y())
        line = self.get_line(p_idx, l_idx) if p_idx >= 0 else None

        if mode == "scrub" or not line or s_idx < 0:
            self._drag_mode = "scrub"
            cs = self.x_to_cs_float(pos.x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()
            return

        if (p_idx, l_idx) != (self.page_idx, self.line_idx):
            self.page_idx = p_idx
            self.line_idx = l_idx
            self.line_clicked.emit(p_idx, l_idx)

        self.selected_syl = s_idx
        self.syllable_selected.emit(s_idx)
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
            elif mode == "move":
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
            new_start = max(min_cs, min(cs, syl.end - 1))
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
            new_end = min(max_cs, max(cs, syl.start + 1))
            syl.end = new_end
            if self._drag_sticky_neighbor is not None:
                line.syllables[self._drag_sticky_neighbor].start = new_end
            self._drag_changed = True
        elif self._drag_mode == "move":
            dur = syl.end - syl.start
            new_start = max(0, cs - self._drag_offset_cs)
            syl.start = new_start
            syl.end = new_start + dur
            self._drag_changed = True

        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "pan":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self._drag_mode in ("left", "right", "move") and self._drag_changed:
            line = self.get_line(self._drag_page_idx, self._drag_line_idx)
            txt = line.syllables[self._drag_syl_idx].text.strip() if line and 0 <= self._drag_syl_idx < len(line.syllables) else ""
            self.project_mutated.emit(f"Align Syllable '{txt}' (P{self._drag_page_idx + 1} L{self._drag_line_idx + 1})")
        self._drag_mode = None
        self._drag_changed = False
        self._drag_sticky_neighbor = None

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

        bar = QHBoxLayout()
        bar.setSpacing(4)
        self._sticky_cb = QCheckBox("Sticky Borders")
        self._sticky_cb.setChecked(True)
        self._sticky_cb.toggled.connect(self._on_sticky)
        bar.addWidget(self._sticky_cb)

        self._follow_cb = QCheckBox("Follow Playhead")
        self._follow_cb.setChecked(True)
        bar.addWidget(self._follow_cb)

        bar.addSpacing(8)
        for label, tip, fn in [
            ("Snap Start", "Snap selected syllable's start to previous lyric's end", self._snap_start),
            ("Snap End", "Snap selected syllable's end to next lyric's start", self._snap_end),
            ("Snap Both", "Snap both start & end to adjacent lyrics", self._snap_both),
            ("Split Syl…", "Split selected syllable (choose text & time)", self._split_syl),
            ("Join Syl", "Join selected syllable with next syllable", self._join_syl),
            ("Delete Syl", "Delete selected syllable", self._delete_syl),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            bar.addWidget(btn)

        bar.addSpacing(10)
        for label, tip, fn in [
            ("Split Line", "Split current line into two lines at the selected syllable", self._split_line),
            ("Join Line", "Join current line with the next line", self._join_line),
            ("Reset Displays", "Reset all Display/Remove windows to valid defaults", self._reset_displays),
            ("Play Line", "Play the selected line", self._play_current_line),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            bar.addWidget(btn)
        bar.addStretch()

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
        self.canvas.project_mutated.connect(self.project_mutated.emit)
        self.canvas.viewport_changed.connect(self._on_canvas_viewport)

        self._hscroll = QScrollBar(Qt.Orientation.Horizontal, right_box)
        self._hscroll.valueChanged.connect(self._on_hscroll)

        right_layout.addWidget(self.canvas, 1)
        right_layout.addWidget(self._hscroll)

        splitter.addWidget(self._line_tree)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(1, 1)

        root.addLayout(bar)
        root.addWidget(splitter, 1)

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
                hdr_label = f"Page {p_idx + 1}  •  OPT" if is_opt else f"Page {p_idx + 1}"
                page_item = QTreeWidgetItem([hdr_label])
                page_item.setFont(0, header_font)
                page_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # section header, not selectable as a line
                page_item.setBackground(0, QBrush(QColor(34, 58, 48) if is_opt else QColor(34, 40, 52)))
                page_item.setForeground(0, QBrush(QColor(135, 225, 175) if is_opt else QColor(155, 185, 225)))
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

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[1] >= 0:
            self._play_current_line()

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        item = self._line_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[1] < 0:
            return
        p_idx, l_idx = data
        self.set_selection(p_idx, l_idx, center=False)

        menu = QMenu(self)
        act_play = QAction("Play Line", self)
        act_play.triggered.connect(self._play_current_line)
        menu.addAction(act_play)

        menu.addSeparator()
        act_split = QAction("Split Line at Selected Syllable", self)
        act_split.triggered.connect(self._split_line)
        menu.addAction(act_split)

        act_join = QAction("Join with Next Line", self)
        act_join.triggered.connect(self._join_line)
        menu.addAction(act_join)

        menu.addSeparator()
        act_reset = QAction("Reset All Display Windows", self)
        act_reset.triggered.connect(self._reset_displays)
        menu.addAction(act_reset)

        menu.exec(self._line_tree.viewport().mapToGlobal(pos))

    def _on_sticky(self, checked: bool) -> None:
        self.canvas.sticky_borders = checked

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

    def _delete_syl(self) -> None:
        line = self.canvas.current_line()
        if not line:
            return
        if line.delete_syllable(self.canvas.selected_syl):
            self.canvas.selected_syl = max(0, min(self.canvas.selected_syl, len(line.syllables) - 1))
            self._rebuild_tree(preserve_view=True, target_page=self.canvas.page_idx, target_line=self.canvas.line_idx)
            self.canvas.update()
            self.project_mutated.emit(f"Delete Syllable (P{self.canvas.page_idx + 1} L{self.canvas.line_idx + 1})")

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
        self.project.reset_windows()
        self.canvas.update()
        self.project_mutated.emit("Reset All Display Windows")

    def _play_current_line(self) -> None:
        line = self.canvas.current_line()
        if line:
            self.play_line_requested.emit(max(0, line.first_syl_start - 30), line.last_syl_end + 30)
