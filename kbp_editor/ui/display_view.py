from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QSettings, Qt, QLineF, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import (
    DEFAULT_GAP_THRESHOLD_CS,
    DEFAULT_OPTIMIZED_LEAD_IN_CS,
    KbpProject,
    Line,
)
from .common import format_time_cs, style_label_color


LABEL_COL_W = 240
RULER_H = 28
ROW_H = 26
PAGE_HDR_H = 24
PAGE_GAP = 4
HANDLE_HIT_PX = 8


DEFAULT_DISPLAY_PX_PER_CS = 0.25
_TIME_STEPS_CS = (5, 10, 25, 50, 100, 200, 500, 1000, 1500, 3000, 6000)


def _nice_step_cs(px_per_cs: float, min_px: float) -> int:
    for step in _TIME_STEPS_CS:
        if step * px_per_cs >= min_px:
            return step
    return _TIME_STEPS_CS[-1]


class _DisplayTimelineCanvas(QWidget):
    """Interactive horizontal Display/Remove timeline canvas."""

    playhead_scrubbed = Signal(float)
    selection_changed = Signal(int, int)      # page_idx, line_idx
    project_mutated = Signal(str)             # undo action description
    viewport_changed = Signal(float, float)   # start_cs, span_cs

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project: Optional[KbpProject] = None
        self.playhead_cs: float = 0.0
        self.selected_page: int = 0
        self.selected_line: int = 0

        self.px_per_cs: float = 0.25  # 25 px per second
        self.scroll_x_cs: float = 0.0
        self.scroll_y_px: int = 0

        self.page_display_mode: bool = False
        self.page_removal_mode: bool = False

        self._rows: List[Tuple[int, int, int, Line]] = []
        self._page_headers: List[Tuple[int, int]] = []  # (raw_y, page_idx)
        self._content_h: int = 0

        self._drag_mode: Optional[str] = None  # "scrub", "start", "end", "pan"
        self._drag_page: int = 0
        self._drag_line: int = 0
        self._drag_changed: bool = False
        self._pan_start_x: float = 0.0
        self._pan_start_scroll: float = 0.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_project(self, project: Optional[KbpProject]) -> None:
        self.project = project
        self._rebuild_rows()
        self.update()

    def _rebuild_rows(self) -> None:
        self._rows.clear()
        self._page_headers.clear()
        if not self.project:
            self._content_h = 0
            return
        y = RULER_H + 2
        for p_idx, page in enumerate(self.project.pages):
            self._page_headers.append((y, p_idx))
            y += PAGE_HDR_H
            for l_idx, line in enumerate(page.lines):
                self._rows.append((y, p_idx, l_idx, line))
                y += ROW_H
            y += PAGE_GAP
        self._content_h = y

    def view_span_cs(self) -> float:
        return max(100.0, (self.width() - LABEL_COL_W) / max(1e-6, self.px_per_cs))

    def _emit_viewport(self) -> None:
        self.viewport_changed.emit(self.scroll_x_cs, self.view_span_cs())

    def set_scroll_x_cs(self, cs: float) -> None:
        self.scroll_x_cs = max(0.0, float(cs))
        self._emit_viewport()
        self.update()

    def center_on_cs(self, cs: float) -> None:
        self.set_scroll_x_cs(cs - self.view_span_cs() * 0.5)

    def scroll_to_row(self, page_idx: int, line_idx: int) -> None:
        for raw_y, p_idx, l_idx, line in self._rows:
            if p_idx == page_idx and l_idx == line_idx:
                mid_cs = (line.display_start + line.display_end) * 0.5
                self.center_on_cs(mid_cs)
                # Also ensure vertically visible
                if raw_y - self.scroll_y_px < RULER_H or raw_y - self.scroll_y_px + ROW_H > self.height():
                    self.scroll_y_px = max(0, raw_y - RULER_H - 60)
                self.update()
                return

    def cs_to_x(self, cs: float) -> float:
        return LABEL_COL_W + (cs - self.scroll_x_cs) * self.px_per_cs

    def x_to_cs(self, x: float) -> int:
        return max(0, int(round(self.scroll_x_cs + (x - LABEL_COL_W) / max(1e-6, self.px_per_cs))))

    def x_to_cs_float(self, x: float) -> float:
        return max(0.0, self.scroll_x_cs + (x - LABEL_COL_W) / max(1e-6, self.px_per_cs))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._emit_viewport()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(24, 26, 31))

        if not self.project:
            painter.setPen(QColor(150, 155, 165))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drag & drop a .kbp file anywhere to edit Display / Remove windows.")
            return

        self._paint_grid(painter, w, h)
        self._paint_rows(painter, w, h)
        self._paint_ruler(painter, w)
        self._paint_playhead(painter, h)

    def _paint_grid(self, painter: QPainter, w: int, h: int) -> None:
        step_cs = _nice_step_cs(self.px_per_cs, min_px=24.0)
        major_cs = _nice_step_cs(self.px_per_cs, min_px=76.0)

        start_cs = int(self.scroll_x_cs // step_cs) * step_cs
        end_cs = self.x_to_cs(w) + step_cs
        for t in range(start_cs, end_cs + 1, step_cs):
            x = int(self.cs_to_x(t))
            if x >= LABEL_COL_W:
                is_major = (t % major_cs == 0)
                painter.setPen(QPen(QColor(52, 57, 67) if is_major else QColor(36, 40, 47), 1))
                painter.drawLine(x, RULER_H, x, h)

    def _paint_ruler(self, painter: QPainter, w: int) -> None:
        painter.fillRect(0, 0, w, RULER_H, QColor(33, 37, 44))
        painter.setPen(QColor(70, 75, 86))
        painter.drawLine(0, RULER_H - 1, w, RULER_H - 1)
        painter.drawLine(LABEL_COL_W, 0, LABEL_COL_W, self.height())

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(175, 182, 195))
        painter.drawText(8, 0, LABEL_COL_W - 16, RULER_H, Qt.AlignmentFlag.AlignVCenter, "Page / Line (click to jump)")

        minor_cs = _nice_step_cs(self.px_per_cs, min_px=24.0)
        major_cs = _nice_step_cs(self.px_per_cs, min_px=76.0)
        start_cs = int(self.scroll_x_cs // minor_cs) * minor_cs
        end_cs = self.x_to_cs(w) + major_cs

        painter.save()
        painter.setClipRect(LABEL_COL_W + 1, 0, max(0, w - LABEL_COL_W - 1), RULER_H)
        for t in range(start_cs, end_cs + 1, minor_cs):
            x = int(self.cs_to_x(t))
            if x < LABEL_COL_W:
                continue
            is_major = (t % major_cs == 0)
            painter.setPen(QColor(115, 124, 138) if is_major else QColor(72, 78, 90))
            tick_h = 8 if is_major else 4
            painter.drawLine(x, RULER_H - tick_h, x, RULER_H - 1)
            if is_major:
                prec = 0 if major_cs >= 100 else (1 if major_cs >= 10 else 2)
                lbl = format_time_cs(t, pad_minutes=False, precision=prec)
                painter.setPen(QColor(195, 202, 215))
                painter.drawText(x + 4, 2, 68, RULER_H - 6, Qt.AlignmentFlag.AlignVCenter, lbl)
        painter.restore()

    def _paint_rows(self, painter: QPainter, w: int, h: int) -> None:
        # 1. Paint Page section headers matching the Lyrics Wiping left bar
        hdr_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        for raw_y, p_idx in self._page_headers:
            y = raw_y - self.scroll_y_px
            if y + PAGE_HDR_H < RULER_H or y > h:
                continue
            is_opt = bool(self.project and self.project.is_optimized_lead_in_page(p_idx))
            hdr_bg = QColor(34, 58, 48) if is_opt else QColor(34, 40, 52)
            hdr_fg = QColor(135, 225, 175) if is_opt else QColor(155, 185, 225)
            # Subtle full-row divider bar + rich Page header in left column
            painter.fillRect(0, y, LABEL_COL_W, PAGE_HDR_H, hdr_bg)
            painter.fillRect(LABEL_COL_W + 1, y, w - LABEL_COL_W, PAGE_HDR_H, QColor(hdr_bg.red(), hdr_bg.green(), hdr_bg.blue(), 65))
            painter.setFont(hdr_font)
            painter.setPen(hdr_fg)
            painter.drawText(8, y, LABEL_COL_W - 14, PAGE_HDR_H, Qt.AlignmentFlag.AlignVCenter, f"Page {p_idx + 1}")

        # 2. Paint individual lyric line rows
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        fm = painter.fontMetrics()

        for raw_y, p_idx, l_idx, line in self._rows:
            y = raw_y - self.scroll_y_px
            if y + ROW_H < RULER_H or y > h:
                continue

            is_selected = (p_idx == self.selected_page and l_idx == self.selected_line)
            is_sel_page = (p_idx == self.selected_page)

            if is_selected:
                bg = QColor(43, 91, 132)
                timeline_bg = QColor(40, 52, 70)
            elif is_sel_page:
                bg = QColor(28, 32, 40)
                timeline_bg = QColor(30, 35, 44)
            else:
                bg = QColor(25, 28, 34)
                timeline_bg = QColor(25, 28, 34) if p_idx % 2 == 0 else QColor(22, 24, 30)
            painter.fillRect(0, y, LABEL_COL_W, ROW_H - 1, bg)
            painter.fillRect(LABEL_COL_W + 1, y, w - LABEL_COL_W, ROW_H - 1, timeline_bg)

            painter.save()
            painter.setClipRect(0, y, LABEL_COL_W - 2, ROW_H)
            if line.is_spacer:
                txt = "(blank spacer)"
            else:
                raw_text = line.full_text.strip() or "(empty)"
                if line.is_fixed:
                    txt = f"[{line.style}·Fixed] {raw_text}"
                elif line.style != "A":
                    txt = f"[{line.style}] {raw_text}"
                else:
                    txt = raw_text
            elided = fm.elidedText(txt, Qt.TextElideMode.ElideRight, LABEL_COL_W - 22)
            if is_selected:
                pen_c = QColor(255, 255, 255)
            elif line.is_spacer:
                pen_c = QColor(105, 112, 126)
            elif line.is_fixed:
                pen_c = QColor(140, 200, 225)
            elif line.style != "A":
                pen_c = style_label_color(line.style)
            else:
                pen_c = QColor(210, 216, 226)
            painter.setPen(pen_c)
            painter.drawText(16, y, LABEL_COL_W - 20, ROW_H - 1, Qt.AlignmentFlag.AlignVCenter, elided)
            painter.restore()

            if line.is_spacer:
                continue

            painter.save()
            painter.setClipRect(LABEL_COL_W + 1, y, w - LABEL_COL_W, ROW_H)

            x_disp_start = self.cs_to_x(line.display_start)
            x_syl_start = self.cs_to_x(line.first_syl_start)
            x_syl_end = self.cs_to_x(line.last_syl_end)
            x_disp_end = self.cs_to_x(line.display_end)

            bar_y = y + 4
            bar_h = ROW_H - 10

            if line.is_fixed:
                # Fixed-text lines don't wipe; render a solid calm blue-teal window bar
                painter.fillRect(QRectF(x_disp_start, bar_y, max(2.0, x_disp_end - x_disp_start), bar_h), QColor(68, 122, 148))
            else:
                first_timed_idx = self.project.pages[p_idx].first_timed_line_index if self.project else 0
                if x_syl_start > x_disp_start:
                    lead_color = (
                        QColor(85, 130, 105)
                        if (l_idx == first_timed_idx and self.project and self.project.is_optimized_lead_in_page(p_idx))
                        else QColor(108, 114, 126)
                    )
                    painter.fillRect(QRectF(x_disp_start, bar_y, x_syl_start - x_disp_start, bar_h), lead_color)

                painter.fillRect(QRectF(x_syl_start, bar_y, max(2.0, x_syl_end - x_syl_start), bar_h), QColor(235, 132, 34))

                if x_disp_end > x_syl_end:
                    painter.fillRect(QRectF(x_syl_end, bar_y, x_disp_end - x_syl_end, bar_h), QColor(108, 114, 126))

            border_c = QColor(255, 255, 255) if is_selected else QColor(55, 60, 70)
            painter.setPen(QPen(border_c, 1))
            painter.drawRect(QRectF(x_disp_start, bar_y, max(2.0, x_disp_end - x_disp_start), bar_h))

            painter.fillRect(QRectF(x_disp_start - 2, bar_y - 1, 4, bar_h + 2), QColor(120, 210, 255))
            painter.fillRect(QRectF(x_disp_end - 2, bar_y - 1, 4, bar_h + 2), QColor(255, 190, 110))
            painter.restore()

    def _paint_playhead(self, painter: QPainter, h: int) -> None:
        x = self.cs_to_x(self.playhead_cs)
        if x >= LABEL_COL_W:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(255, 70, 90), 2.0))
            painter.drawLine(QLineF(x, 0.0, x, float(h)))
            painter.restore()

    def _hit_test(self, x: float, y: float) -> Tuple[Optional[str], int, int]:
        if y < RULER_H and x >= LABEL_COL_W:
            return ("scrub", self.selected_page, self.selected_line)
        if x < LABEL_COL_W:
            for raw_y, p_idx in self._page_headers:
                ry = raw_y - self.scroll_y_px
                if ry <= y <= ry + PAGE_HDR_H:
                    return ("select", p_idx, 0)
        for raw_y, p_idx, l_idx, line in self._rows:
            ry = raw_y - self.scroll_y_px
            if ry <= y <= ry + ROW_H:
                if x < LABEL_COL_W:
                    return ("select", p_idx, l_idx)
                if line.is_spacer:
                    return ("select_scrub", p_idx, l_idx)
                x_s = self.cs_to_x(line.display_start)
                x_e = self.cs_to_x(line.display_end)
                if abs(x - x_s) <= HANDLE_HIT_PX:
                    return ("start", p_idx, l_idx)
                if abs(x - x_e) <= HANDLE_HIT_PX:
                    return ("end", p_idx, l_idx)
                return ("select_scrub", p_idx, l_idx)
        return (None, self.selected_page, self.selected_line)

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

        hit, p_idx, l_idx = self._hit_test(pos.x(), pos.y())
        if hit is None:
            return

        if hit in ("select", "select_scrub", "start", "end"):
            self.selected_page = p_idx
            self.selected_line = l_idx
            self.selection_changed.emit(p_idx, l_idx)

        if hit == "select":
            # Clicking the left label immediately jumps the timeline to that line!
            self.scroll_to_row(p_idx, l_idx)
        elif hit in ("scrub", "select_scrub"):
            self._drag_mode = "scrub"
            cs = self.x_to_cs_float(pos.x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()
        elif hit in ("start", "end"):
            self._drag_mode = hit
            self._drag_page = p_idx
            self._drag_line = l_idx
            self._drag_changed = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.project:
            return
        pos = event.position()

        if self._drag_mode is None:
            hit, _, _ = self._hit_test(pos.x(), pos.y())
            if hit in ("start", "end"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit == "select":
                self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        page = self.project.pages[self._drag_page]
        line = page.lines[self._drag_line]

        if self._drag_mode == "start":
            if self.page_display_mode:
                clamped = min(cs, page.first_syl_start)
                for ln in page.lines:
                    if ln.is_spacer:
                        continue
                    cap = ln.display_end if ln.is_fixed else ln.first_syl_start
                    ln.display_start = max(0, min(clamped, cap))
            elif not line.is_spacer:
                cap = line.display_end if line.is_fixed else line.first_syl_start
                line.display_start = max(0, min(cs, cap))
            self._drag_changed = True
            self.update()
        elif self._drag_mode == "end":
            if self.page_removal_mode:
                clamped = max(cs, page.last_syl_end)
                for ln in page.lines:
                    if ln.is_spacer:
                        continue
                    floor_cs = ln.display_start if ln.is_fixed else ln.last_syl_end
                    ln.display_end = max(clamped, floor_cs)
            elif not line.is_spacer:
                floor_cs = line.display_start if line.is_fixed else line.last_syl_end
                line.display_end = max(cs, floor_cs)
            self._drag_changed = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "pan":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self._drag_mode in ("start", "end") and self._drag_changed:
            kind = "Display Start" if self._drag_mode == "start" else "Display End"
            scope = f"Page {self._drag_page + 1}" if (
                (self._drag_mode == "start" and self.page_display_mode)
                or (self._drag_mode == "end" and self.page_removal_mode)
            ) else f"P{self._drag_page + 1} L{self._drag_line + 1}"
            self.project_mutated.emit(f"Adjust {kind} ({scope})")
        self._drag_mode = None
        self._drag_changed = False

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if delta > 0 else 1.0 / 1.2
            mouse_cs = self.x_to_cs_float(event.position().x())
            self.px_per_cs = max(0.03, min(4.0, self.px_per_cs * factor))
            self.set_scroll_x_cs(mouse_cs - (event.position().x() - LABEL_COL_W) / self.px_per_cs)
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step = -delta / max(0.05, self.px_per_cs) * 0.4
            self.set_scroll_x_cs(self.scroll_x_cs + step)
        else:
            self.scroll_y_px = max(0, min(max(0, self._content_h - self.height() + 40), self.scroll_y_px - int(delta * 0.5)))
            self.update()


class DisplayRemoveView(QWidget):
    """Display / Remove Window tab with Reset to Default & Optimized Windows controls."""

    playhead_scrubbed = Signal(float)
    selection_changed = Signal(int, int)
    project_mutated = Signal(str)
    viewport_changed = Signal(float, float)
    auto_reset_display_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project: Optional[KbpProject] = None
        self._settings = QSettings("MattJoy", "MattJoyKaraokeBuilder")

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        bar1 = QHBoxLayout()
        self._page_disp_cb = QCheckBox("Page Display")
        self._page_disp_cb.setChecked(self._settings.value("display_page_display", False, type=bool))
        self._page_disp_cb.setToolTip("Drag one left handle to move display_start for the entire page")
        self._page_disp_cb.toggled.connect(self._on_modes_changed)
        bar1.addWidget(self._page_disp_cb)

        self._page_rem_cb = QCheckBox("Page Removal")
        self._page_rem_cb.setChecked(self._settings.value("display_page_removal", False, type=bool))
        self._page_rem_cb.setToolTip("Drag one right handle to move display_end for the entire page")
        self._page_rem_cb.toggled.connect(self._on_modes_changed)
        bar1.addWidget(self._page_rem_cb)

        self._follow_cb = QCheckBox("Follow Playhead")
        self._follow_cb.setChecked(self._settings.value("display_follow_playhead", True, type=bool))
        self._follow_cb.toggled.connect(self._on_display_prefs_changed)
        bar1.addWidget(self._follow_cb)

        self._auto_reset_cb = QCheckBox("Auto Reset Display")
        self._auto_reset_cb.setChecked(True)
        self._auto_reset_cb.setToolTip(
            "When ON: automatically resets display windows & applies ➣➣➣ optimizations on every edit.\n"
            "When OFF: manual Display/Remove window control (and shows DISP/REM lines in Lyrics Wiping)."
        )
        self._auto_reset_cb.toggled.connect(self.auto_reset_display_changed.emit)
        bar1.addWidget(self._auto_reset_cb)

        bar1.addSpacing(12)
        btn_snap_s = QPushButton("Snap Start → Playhead")
        btn_snap_s.clicked.connect(self._snap_start_to_playhead)
        bar1.addWidget(btn_snap_s)

        btn_snap_e = QPushButton("Snap End → Playhead")
        btn_snap_e.clicked.connect(self._snap_end_to_playhead)
        bar1.addWidget(btn_snap_e)

        bar1.addSpacing(12)
        self._fixed_cb = QCheckBox("Fixed Text (No Wipe)")
        self._fixed_cb.setToolTip(
            "Toggle KBP Fixed Text mode (lowercase style letter) for the selected line.\n"
            "Fixed text stays visible across the page without wiping (e.g. singer labels)."
        )
        self._fixed_cb.toggled.connect(self._on_fixed_toggled)
        bar1.addWidget(self._fixed_cb)
        bar1.addStretch()

        bar2 = QHBoxLayout()
        btn_reset_page = QPushButton("Reset Page to Default")
        btn_reset_page.clicked.connect(lambda: self._reset_windows(all_pages=False))
        bar2.addWidget(btn_reset_page)

        btn_reset_all = QPushButton("Reset All to Default")
        btn_reset_all.clicked.connect(lambda: self._reset_windows(all_pages=True))
        bar2.addWidget(btn_reset_all)

        bar2.addSpacing(14)
        bar2.addWidget(QLabel("➣➣➣ Lead-in (cs):"))
        self._opt_lead_spin = QSpinBox()
        self._opt_lead_spin.setRange(0, 300)
        self._opt_lead_spin.setValue(self._settings.value("display_opt_lead_in_cs", DEFAULT_OPTIMIZED_LEAD_IN_CS, type=int))
        self._opt_lead_spin.setFixedWidth(64)
        self._opt_lead_spin.valueChanged.connect(self._on_display_prefs_changed)
        bar2.addWidget(self._opt_lead_spin)

        bar2.addWidget(QLabel("Gap ≥ (cs):"))
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(50, 2000)
        self._gap_spin.setSingleStep(50)
        self._gap_spin.setValue(self._settings.value("display_gap_threshold_cs", DEFAULT_GAP_THRESHOLD_CS, type=int))
        self._gap_spin.setFixedWidth(70)
        self._gap_spin.valueChanged.connect(self._on_display_prefs_changed)
        bar2.addWidget(self._gap_spin)

        btn_opt_page = QPushButton("Optimize Page")
        btn_opt_page.clicked.connect(lambda: self._optimize_windows(all_pages=False))
        bar2.addWidget(btn_opt_page)

        btn_opt_all = QPushButton("Optimized Windows (All)")
        btn_opt_all.setStyleSheet("background-color: #2d6a4f; color: white; font-weight: bold; padding: 4px 10px;")
        btn_opt_all.clicked.connect(lambda: self._optimize_windows(all_pages=True))
        bar2.addWidget(btn_opt_all)
        bar2.addStretch()

        self.canvas = _DisplayTimelineCanvas(self)
        self.canvas.page_display_mode = self._page_disp_cb.isChecked()
        self.canvas.page_removal_mode = self._page_rem_cb.isChecked()
        self.canvas.playhead_scrubbed.connect(self.playhead_scrubbed.emit)
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self.canvas.project_mutated.connect(self.project_mutated.emit)
        self.canvas.viewport_changed.connect(self._on_canvas_viewport)

        self._hscroll = QScrollBar(Qt.Orientation.Horizontal, self)
        self._hscroll.valueChanged.connect(self._on_hscroll)

        root.addLayout(bar1)
        root.addLayout(bar2)
        root.addWidget(self.canvas, 1)
        root.addWidget(self._hscroll)

    def set_auto_reset_display(self, enabled: bool) -> None:
        self._auto_reset_cb.blockSignals(True)
        self._auto_reset_cb.setChecked(bool(enabled))
        self._auto_reset_cb.blockSignals(False)

    def set_project(self, project: Optional[KbpProject], preserve_view: bool = False) -> None:
        old_page = self.canvas.selected_page
        old_line = self.canvas.selected_line
        old_sx = self.canvas.scroll_x_cs
        old_sy = self.canvas.scroll_y_px

        self.project = project
        self.canvas.set_project(project)
        if project:
            max_cs = max(1000, project.total_duration_cs + 500)
            self._hscroll.blockSignals(True)
            self._hscroll.setRange(0, max_cs)
            self._hscroll.setPageStep(int(self.canvas.view_span_cs()))
            if preserve_view:
                p_idx = min(old_page, max(0, len(project.pages) - 1))
                l_idx = min(old_line, max(0, len(project.pages[p_idx].lines) - 1)) if project.pages else 0
                self.canvas.selected_page = p_idx
                self.canvas.selected_line = l_idx
                self.canvas.scroll_y_px = old_sy
                self.canvas.set_scroll_x_cs(old_sx)
                self._hscroll.setValue(int(old_sx))
            self._hscroll.blockSignals(False)
        self._sync_fixed_checkbox()

    def set_playhead_cs(self, cs: float, follow_if_playing: bool = False) -> None:
        self.canvas.playhead_cs = cs
        if follow_if_playing and self._follow_cb.isChecked():
            span = self.canvas.view_span_cs()
            if cs > self.canvas.scroll_x_cs + span * 0.85 or cs < self.canvas.scroll_x_cs:
                self.canvas.set_scroll_x_cs(cs - span * 0.15)
                return
        self.canvas.update()

    def set_selection(self, page_idx: int, line_idx: int, scroll_to: bool = False) -> None:
        self.canvas.selected_page = page_idx
        self.canvas.selected_line = line_idx
        self._sync_fixed_checkbox()
        if scroll_to:
            self.canvas.scroll_to_row(page_idx, line_idx)
        else:
            self.canvas.update()

    def _on_canvas_selection_changed(self, page_idx: int, line_idx: int) -> None:
        self._sync_fixed_checkbox()
        self.selection_changed.emit(page_idx, line_idx)

    def _sync_fixed_checkbox(self) -> None:
        self._fixed_cb.blockSignals(True)
        if (
            self.project
            and 0 <= self.canvas.selected_page < len(self.project.pages)
            and 0 <= self.canvas.selected_line < len(self.project.pages[self.canvas.selected_page].lines)
        ):
            ln = self.project.pages[self.canvas.selected_page].lines[self.canvas.selected_line]
            self._fixed_cb.setEnabled(not ln.is_spacer)
            self._fixed_cb.setChecked(ln.is_fixed)
        else:
            self._fixed_cb.setEnabled(False)
            self._fixed_cb.setChecked(False)
        self._fixed_cb.blockSignals(False)

    def _on_fixed_toggled(self, checked: bool) -> None:
        if (
            not self.project
            or not (0 <= self.canvas.selected_page < len(self.project.pages))
            or not (0 <= self.canvas.selected_line < len(self.project.pages[self.canvas.selected_page].lines))
        ):
            return
        ln = self.project.pages[self.canvas.selected_page].lines[self.canvas.selected_line]
        if ln.is_spacer or ln.is_fixed == checked:
            return
        ln.set_fixed(checked)
        self.canvas.update()
        state = "ON" if checked else "OFF"
        self.project_mutated.emit(
            f"Fixed Text {state} (P{self.canvas.selected_page + 1} L{self.canvas.selected_line + 1})"
        )

    def jump_to_cs(self, cs: float) -> None:
        self.canvas.center_on_cs(cs)

    def zoom(self, factor: float) -> None:
        center_cs = self.canvas.scroll_x_cs + self.canvas.view_span_cs() * 0.5
        self.canvas.px_per_cs = max(0.03, min(4.0, self.canvas.px_per_cs * factor))
        self.canvas.center_on_cs(center_cs)

    def reset_zoom(self) -> None:
        center_cs = self.canvas.scroll_x_cs + self.canvas.view_span_cs() * 0.5
        self.canvas.px_per_cs = DEFAULT_DISPLAY_PX_PER_CS
        self.canvas.center_on_cs(center_cs)

    def _on_canvas_viewport(self, start_cs: float, span_cs: float) -> None:
        self._hscroll.blockSignals(True)
        self._hscroll.setPageStep(max(100, int(span_cs)))
        self._hscroll.setValue(int(start_cs))
        self._hscroll.blockSignals(False)
        self.viewport_changed.emit(start_cs, span_cs)

    def _on_modes_changed(self) -> None:
        self.canvas.page_display_mode = self._page_disp_cb.isChecked()
        self.canvas.page_removal_mode = self._page_rem_cb.isChecked()
        self._settings.setValue("display_page_display", self.canvas.page_display_mode)
        self._settings.setValue("display_page_removal", self.canvas.page_removal_mode)
        self._settings.sync()

    def _on_display_prefs_changed(self) -> None:
        self._settings.setValue("display_follow_playhead", self._follow_cb.isChecked())
        self._settings.setValue("display_opt_lead_in_cs", self._opt_lead_spin.value())
        self._settings.setValue("display_gap_threshold_cs", self._gap_spin.value())
        self._settings.sync()

    def _on_hscroll(self, value: int) -> None:
        self.canvas.set_scroll_x_cs(float(value))

    def _snap_start_to_playhead(self) -> None:
        if not self.project or not self.project.pages:
            return
        page = self.project.pages[self.canvas.selected_page]
        cs = int(round(self.canvas.playhead_cs))
        if self._page_disp_cb.isChecked():
            for ln in page.lines:
                if ln.is_spacer:
                    continue
                cap = ln.display_end if ln.is_fixed else ln.first_syl_start
                ln.display_start = max(0, min(cs, cap))
            self.project_mutated.emit(f"Snap Page {self.canvas.selected_page + 1} Display Start")
        else:
            ln = page.lines[self.canvas.selected_line]
            if not ln.is_spacer:
                cap = ln.display_end if ln.is_fixed else ln.first_syl_start
                ln.display_start = max(0, min(cs, cap))
                self.project_mutated.emit(f"Snap P{self.canvas.selected_page + 1} L{self.canvas.selected_line + 1} Display Start")
        self.canvas.update()

    def _snap_end_to_playhead(self) -> None:
        if not self.project or not self.project.pages:
            return
        page = self.project.pages[self.canvas.selected_page]
        cs = int(round(self.canvas.playhead_cs))
        if self._page_rem_cb.isChecked():
            for ln in page.lines:
                if ln.is_spacer:
                    continue
                floor_cs = ln.display_start if ln.is_fixed else ln.last_syl_end
                ln.display_end = max(cs, floor_cs)
            self.project_mutated.emit(f"Snap Page {self.canvas.selected_page + 1} Display End")
        else:
            ln = page.lines[self.canvas.selected_line]
            if not ln.is_spacer:
                floor_cs = ln.display_start if ln.is_fixed else ln.last_syl_end
                ln.display_end = max(cs, floor_cs)
                self.project_mutated.emit(f"Snap P{self.canvas.selected_page + 1} L{self.canvas.selected_line + 1} Display End")
        self.canvas.update()

    def _reset_windows(self, all_pages: bool) -> None:
        if not self.project or not self.project.pages:
            return
        if all_pages:
            self.project.reset_windows()
            self.project_mutated.emit("Reset All Windows to Default")
        else:
            p_idx = self.canvas.selected_page
            self.project.reset_windows(page_indices=[p_idx])
            self.project_mutated.emit(f"Reset Page {p_idx + 1} Windows to Default")
        self.canvas.update()

    def _optimize_windows(self, all_pages: bool) -> None:
        if not self.project or not self.project.pages:
            return
        opt_lead = self._opt_lead_spin.value()
        gap_thr = self._gap_spin.value()
        if all_pages:
            self.project.optimize_windows(
                optimized_lead_in_cs=opt_lead,
                gap_threshold_cs=gap_thr,
            )
            self.project_mutated.emit("Apply Optimized Windows (All)")
        else:
            p_idx = self.canvas.selected_page
            self.project.optimize_windows(
                page_indices=[p_idx],
                optimized_lead_in_cs=opt_lead,
                gap_threshold_cs=gap_thr,
            )
            self.project_mutated.emit(f"Apply Optimized Windows (Page {p_idx + 1})")
        self.canvas.update()
