from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QLineF, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..audio import AudioEngine
from ..model import KbpProject


class SongMinimapWidget(QWidget):
    """
    Full-song overview scrubber showing waveform, labeled Page blocks (P1..PN),
    current visible viewport highlight, and red playhead.
    Click or drag anywhere to jump rapidly to any part of the song.
    """

    jump_requested = Signal(float)   # centiseconds (centers view & optionally scrubs)
    page_clicked = Signal(int)       # 0-based page_idx

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.project: Optional[KbpProject] = None
        self.playhead_cs: float = 0.0
        self.view_start_cs: float = 0.0
        self.view_span_cs: float = 1500.0
        self.selected_page: int = 0
        self._dragging: bool = False
        self._bg_cache: Optional[QPixmap] = None
        self._bg_cache_key: Optional[Tuple] = None
        self._cache_rev: int = 0

        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Song Overview Minimap — click or drag to jump anywhere in the song")

    def invalidate_cache(self) -> None:
        self._cache_rev += 1
        self._bg_cache = None
        self._bg_cache_key = None
        self.update()

    @property
    def total_cs(self) -> float:
        proj_end = float(self.project.total_duration_cs) if self.project else 0.0
        aud_end = float(self.audio.duration_cs) if self.audio.duration_cs else 0.0
        return max(1000.0, proj_end + 300.0, aud_end)

    def set_project(self, project: Optional[KbpProject]) -> None:
        self.project = project
        self.invalidate_cache()

    def set_playhead_cs(self, cs: float) -> None:
        if abs(cs - self.playhead_cs) > 1e-3:
            self.playhead_cs = cs
            self.update()

    def set_viewport(self, start_cs: float, span_cs: float) -> None:
        self.view_start_cs = max(0.0, start_cs)
        self.view_span_cs = max(50.0, span_cs)
        self.update()

    def set_selected_page(self, page_idx: int) -> None:
        if page_idx != self.selected_page:
            self.selected_page = page_idx
            self.invalidate_cache()

    def cs_to_x(self, cs: float) -> float:
        return (cs / self.total_cs) * self.width()

    def x_to_cs(self, x: float) -> float:
        w = max(1, self.width())
        return max(0.0, min(self.total_cs, (x / w) * self.total_cs))

    def _rebuild_bg_cache(self, w: int, h: int) -> QPixmap:
        pm = QPixmap(w, h)
        pm.fill(QColor(18, 20, 26))
        painter = QPainter(pm)
        painter.setPen(QColor(50, 56, 68))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Faint full-song waveform (vectorized)
        wf = self.audio.active_waveform
        if wf is not None and len(wf.peaks) > 0 and w > 0:
            peaks = wf.peaks
            n = len(peaks)
            mid_y = h * 0.62
            half_h = h * 0.32
            total_cs = self.total_cs
            scale_b = wf.buckets_per_sec / 100.0
            px_arr = np.arange(0, w, 2, dtype=np.float64)
            cs0 = (px_arr / max(1, w)) * total_cs
            cs1 = ((px_arr + 2.0) / max(1, w)) * total_cs
            i0 = np.clip((cs0 * scale_b).astype(np.int64), 0, n - 1)
            i1 = np.clip((cs1 * scale_b).astype(np.int64) + 1, i0 + 1, n)
            amps = np.maximum.reduceat(peaks, i0)
            amps[-1] = float(peaks[int(i0[-1]):int(i1[-1])].max())
            small = (i1 - i0) <= 1
            if np.any(small):
                amps[small] = peaks[i0[small]]
            bars = amps * half_h
            lines = [
                QLineF(px, y0, px, y1)
                for px, y0, y1 in zip(px_arr.tolist(), (mid_y - bars).tolist(), (mid_y + bars).tolist())
            ]
            painter.setPen(QPen(QColor(72, 108, 155, 130), 1))
            painter.drawLines(lines)

        # Page blocks (P1 .. PN)
        if self.project:
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            for p_idx, page in enumerate(self.project.pages):
                if not page.lines:
                    continue
                x0 = self.cs_to_x(page.display_start)
                x1 = self.cs_to_x(page.display_end)
                bw = max(6.0, x1 - x0)
                is_sel = (p_idx == self.selected_page)
                is_opt = self.project.is_optimized_lead_in_page(p_idx)
                if is_sel:
                    fill = QColor(235, 132, 34, 150)
                    border = QColor(255, 210, 120)
                elif is_opt:
                    fill = QColor(45, 135, 95, 115)
                    border = QColor(90, 200, 145)
                else:
                    fill = QColor(65, 115, 175, 105)
                    border = QColor(115, 165, 220)

                rect = QRectF(x0, 4, bw, h - 8)
                painter.fillRect(rect, fill)
                painter.setPen(QPen(border, 1))
                painter.drawRect(rect)

                if bw >= 18:
                    painter.setPen(QColor(245, 248, 255))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"P{p_idx + 1}")

        painter.end()
        return pm

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        cache_key = (w, h, id(self.audio.active_waveform), self.selected_page, self._cache_rev)
        if self._bg_cache is None or self._bg_cache_key != cache_key:
            self._bg_cache = self._rebuild_bg_cache(w, h)
            self._bg_cache_key = cache_key

        painter.drawPixmap(0, 0, self._bg_cache)

        # Visible viewport rectangle
        vx0 = self.cs_to_x(self.view_start_cs)
        vx1 = self.cs_to_x(self.view_start_cs + self.view_span_cs)
        vrect = QRectF(vx0, 1, max(8.0, vx1 - vx0), h - 2)
        painter.fillRect(vrect, QColor(255, 255, 255, 28))
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        painter.drawRect(vrect)

        # Red playhead (sub-pixel smooth)
        px = self.cs_to_x(self.playhead_cs)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 70, 90), 2.0))
        painter.drawLine(QLineF(px, 0.0, px, float(h)))

    def _handle_pointer(self, x: float) -> None:
        cs = self.x_to_cs(x)
        self.jump_requested.emit(cs)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._handle_pointer(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._handle_pointer(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
