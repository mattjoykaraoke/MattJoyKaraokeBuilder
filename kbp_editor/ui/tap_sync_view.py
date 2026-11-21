from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QSettings, Qt, QLineF, QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..audio import AudioEngine, WaveformData
from ..model import KbpProject
from ..tap_sync import TapLine, TapSyllable, TapSyncDraft, default_drafts_dir
from .project_organizer import ProjectOrganizerDialog


def _autosave_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "AdvancedKBPEditor" / "tap_sync_autosave.json"


class _LyricsInputDialog(QDialog):
    """Modal editor to paste or edit KBS / Local Lyric Splitter lyrics."""

    def __init__(self, draft: TapSyncDraft, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paste / Edit Lyrics for Tap Sync")
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Paste lyrics using <b>/</b> for syllable splits (e.g. <code>ev/ery/thing</code>, "
            "<code>➣➣➣/The snow</code>) and <b>blank lines</b> for page breaks:"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._title_edit = QLineEdit(draft.title)
        self._artist_edit = QLineEdit(draft.artist)
        form.addRow("Title:", self._title_edit)
        form.addRow("Artist:", self._artist_edit)
        layout.addLayout(form)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText(
            "➣➣➣/The snow has come down\n"
            "on top of ev/ery/thing\n"
            "\n"
            "➣➣➣/True love\n"
            "The stars in the sky il/lu/mi/nate be/low"
        )
        self._text_edit.setFont(QFont("Consolas", 11))
        self._text_edit.setPlainText(draft.to_lyrics_text())
        layout.addWidget(self._text_edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._text_edit.setFocus()

    @property
    def lyrics_text(self) -> str:
        return self._text_edit.toPlainText()

    @property
    def title_text(self) -> str:
        return self._title_edit.text().strip()

    @property
    def artist_text(self) -> str:
        return self._artist_edit.text().strip()


class _RollingTeleprompterWidget(QWidget):
    """
    High-contrast rolling line teleprompter with cross-page lookahead.
    Never blindsides the user with a sudden page flip: upcoming lines from the
    next page are always visible below with a clear page-break badge.
    """

    end_phrase_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.draft: TapSyncDraft = TapSyncDraft()
        self.cursor: Optional[Tuple[int, int, int]] = None      # next syllable to tap
        self.open_coord: Optional[Tuple[int, int, int]] = None  # currently sustaining syllable
        self.is_syncing: bool = False
        self.is_locked: bool = False
        self.punch_line: Optional[Tuple[int, int]] = None
        self.setMinimumHeight(220)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self.is_syncing:
            self.end_phrase_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(18, 21, 27))

        flat_lines = self.draft.flat_lines()
        if not flat_lines:
            painter.setFont(QFont("Segoe UI", 12))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Click 'Paste / Edit Lyrics…' or 'Load Lyrics / .kbp…' to begin Tap Sync.\n\n"
                "Use '/' for syllable splits and blank lines for page breaks.",
            )
            return

        # Find active line index in flat_lines
        active_flat_idx = 0
        focus_coord = self.cursor or self.open_coord
        if focus_coord is not None:
            for idx, (p_i, l_i, _) in enumerate(flat_lines):
                if (p_i, l_i) == (focus_coord[0], focus_coord[1]):
                    active_flat_idx = idx
                    break
        else:
            active_flat_idx = len(flat_lines) - 1

        # Top status pill (Locked / Syncing / Paused phrase / Punch-in / Ready)
        if self.is_locked:
            synced = self.draft.synced_syllables()
            total = self.draft.total_syllables()
            badge_txt = f"🔒 LOCKED ({synced}/{total} syllables synced) — Click 'Unfreeze Tap Sync' above to re-tap or modify"
            badge_bg = QColor(71, 56, 30, 225)
        elif self.punch_line is not None and self.is_syncing:
            badge_txt = f"● RE-TAPPING P{self.punch_line[0] + 1} L{self.punch_line[1] + 1}  (stops automatically at end of line)"
            badge_bg = QColor(180, 83, 9, 210)
        elif self.is_syncing and self.open_coord is None and self.cursor is not None:
            badge_txt = "⏸ WAITING FOR NEXT WORD — Tap Space on the orange syllable"
            badge_bg = QColor(30, 64, 115, 220)
        elif self.is_syncing:
            badge_txt = "● SYNCING — Tap Space on the orange syllable (hold & release Space, or Ctrl/↓/Right-Click for pauses)"
            badge_bg = QColor(22, 101, 52, 210)
        else:
            synced = self.draft.synced_syllables()
            total = self.draft.total_syllables()
            badge_txt = f"READY ({synced}/{total} syllables synced) — Click 'Start Sync' or press Space to begin"
            badge_bg = QColor(38, 46, 60, 210)

        bar_rect = QRectF(16.0, 10.0, float(w - 32), 26.0)
        painter.setBrush(badge_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 5.0, 5.0)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(241, 245, 249))
        painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter, badge_txt)

        # Rolling window: 1 previous line above, active line centered, up to 3 upcoming lines below
        start_idx = max(0, active_flat_idx - 1)
        end_idx = min(len(flat_lines), active_flat_idx + 4)

        top_y = 48.0
        bottom_y = float(h - 28)
        avail_h = max(120.0, bottom_y - top_y)
        active_y = top_y + avail_h * 0.34
        row_step = min(56.0, max(38.0, avail_h / 5.0))

        prev_page_drawn: Optional[int] = None
        for idx in range(start_idx, end_idx):
            p_i, l_i, line = flat_lines[idx]
            rel = idx - active_flat_idx
            y_center = active_y + rel * row_step
            if y_center < top_y + 10 or y_center > bottom_y - 10:
                continue

            # Subtle cross-page divider badge whenever a line starts a new page
            if prev_page_drawn is not None and p_i != prev_page_drawn:
                div_y = y_center - row_step * 0.5
                painter.setPen(QPen(QColor(75, 135, 110, 140), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QLineF(36.0, div_y, float(w - 36), div_y))
                pg_rect = QRectF(w * 0.5 - 38.0, div_y - 9.0, 76.0, 18.0)
                painter.setBrush(QColor(28, 48, 42))
                painter.setPen(QPen(QColor(85, 165, 130), 1))
                painter.drawRoundedRect(pg_rect, 4.0, 4.0)
                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                painter.setPen(QColor(167, 243, 208))
                painter.drawText(pg_rect, Qt.AlignmentFlag.AlignCenter, f"Page {p_i + 1}")
            prev_page_drawn = p_i

            is_active_line = rel == 0
            is_past_line = rel < 0
            font_size = 20 if is_active_line else (14 if rel == 1 else 12)
            font = QFont("Segoe UI", font_size, QFont.Weight.Bold if is_active_line else QFont.Weight.DemiBold)
            painter.setFont(font)
            fm = QFontMetrics(font)

            if is_active_line:
                hl = QRectF(24.0, y_center - row_step * 0.44, float(w - 48), row_step * 0.88)
                painter.setBrush(QColor(30, 38, 52, 200))
                painter.setPen(QPen(QColor(71, 85, 105), 1))
                painter.drawRoundedRect(hl, 6.0, 6.0)

            # Measure total width of syllables so line is centered cleanly
            widths = [fm.horizontalAdvance(s.text) for s in line.syllables]
            total_w = sum(widths)
            x = max(36.0, (w - total_w) * 0.5)
            text_y = y_center + fm.ascent() * 0.36

            for s_idx, (syl, sw) in enumerate(zip(line.syllables, widths)):
                coord = (p_i, l_i, s_idx)
                is_next = coord == self.cursor

                if is_next:
                    # Clean KBS-style active highlight: plain bright orange text, no underline or box
                    color = QColor(245, 145, 35)
                elif syl.has_start:
                    # Done / inactive word: dull dark-mode gray so it recedes into the background
                    color = QColor(74, 82, 96, 150 if is_past_line else 210)
                else:
                    # Upcoming unsynced syllables: alternating crisp white / soft sage for slash-split readability
                    if is_past_line:
                        color = QColor(90, 98, 112, 130)
                    else:
                        alpha = 255 if is_active_line else (195 if rel == 1 else 130)
                        if s_idx % 2 == 0:
                            color = QColor(238, 242, 248, alpha)
                        else:
                            color = QColor(152, 212, 174, alpha)

                painter.setPen(color)
                painter.drawText(int(round(x)), int(round(text_y)), syl.text)
                x += sw


class _TapWaveformStrip(QWidget):
    """
    Live scrolling waveform strip styled to match Lyrics Wiping, with playhead anchored
    at ~32% from the left so upcoming vocal transients are clearly visible ~2.5s ahead.
    Also renders already-tapped syllable blocks live underneath the waveform.
    """

    playhead_scrubbed = Signal(float)

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.draft: TapSyncDraft = TapSyncDraft()
        self.playhead_cs: float = 0.0
        self.px_per_cs: float = 2.4
        self._dragging: bool = False
        self._drag_scroll_left_cs: Optional[float] = None
        # 3-screen-wide waveform tile cache so 60 fps scrolling is a single fast drawPixmap blit
        self._tile_pm: Optional[QPixmap] = None
        self._tile_start_cs: float = 0.0
        self._tile_span_cs: float = 0.0
        self._tile_key: Optional[Tuple] = None
        self.setMinimumHeight(110)
        self.setMaximumHeight(150)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def invalidate_cache(self) -> None:
        self._tile_pm = None
        self._tile_key = None

    def _max_cs(self) -> float:
        if self.audio.duration_cs > 0:
            return float(self.audio.duration_cs)
        max_syl = 0
        for _, _, _, syl in self.draft.flat_syllables():
            if syl.end_cs is not None:
                max_syl = max(max_syl, syl.end_cs)
            elif syl.start_cs is not None:
                max_syl = max(max_syl, syl.start_cs)
        return float(max(max_syl + 200, 6000))

    def _clamp_cs(self, cs: float) -> float:
        return max(0.0, min(self._max_cs(), float(cs)))

    def _scroll_left_cs(self) -> float:
        if self._drag_scroll_left_cs is not None:
            return self._drag_scroll_left_cs
        return self.playhead_cs - (self.width() * 0.32) / max(1e-6, self.px_per_cs)

    def _cs_to_x(self, cs: float) -> float:
        return (cs - self._scroll_left_cs()) * self.px_per_cs

    def _x_to_cs(self, x: float) -> float:
        return self._clamp_cs(self._scroll_left_cs() + x / max(1e-6, self.px_per_cs))

    def _draw_wave_layer_at(
        self, painter: QPainter, wf: WaveformData, w: int, top: int, height: int, scroll_l: float, color: QColor
    ) -> None:
        peaks = wf.peaks
        n = len(peaks)
        if n == 0 or w <= 0 or height <= 0:
            return
        mid_y = top + height * 0.5
        half_h = height * 0.45
        inv_px = 1.0 / max(1e-6, self.px_per_cs)
        scale_b = wf.buckets_per_sec / 100.0

        px_arr = np.arange(0, w, 1, dtype=np.float64)
        t_start = scroll_l + px_arr * inv_px
        t_end = scroll_l + (px_arr + 1.0) * inv_px
        valid = t_end > 0.0
        if not np.any(valid):
            return
        px_v = px_arr[valid]
        i0 = np.clip((t_start[valid] * scale_b).astype(np.int64), 0, n - 1)
        i1 = np.clip((t_end[valid] * scale_b).astype(np.int64) + 1, i0 + 1, n)

        amps = np.maximum.reduceat(peaks, i0)
        amps[-1] = float(peaks[int(i0[-1]) : int(i1[-1])].max())
        small = (i1 - i0) <= 1
        if np.any(small):
            amps[small] = peaks[i0[small]]

        bars = amps * half_h
        lines = [
            QLineF(px, y0, px, y1)
            for px, y0, y1 in zip(px_v.tolist(), (mid_y - bars).tolist(), (mid_y + bars).tolist())
        ]
        painter.setPen(QPen(color, 1))
        painter.drawLines(lines)

    def _ensure_wave_tile(self, w: int, wave_h: int, scroll_l: float, span_cs: float) -> Optional[QPixmap]:
        if w <= 0 or wave_h <= 0:
            return None
        margin_cs = 8.0 / max(1e-6, self.px_per_cs)
        key = (wave_h, round(self.px_per_cs, 5), id(self.audio.main_waveform), id(self.audio.vocal_waveform))
        need_rebuild = (
            self._tile_pm is None
            or self._tile_key != key
            or (scroll_l - margin_cs) < self._tile_start_cs
            or (scroll_l + span_cs + margin_cs) > (self._tile_start_cs + self._tile_span_cs)
        )
        if need_rebuild:
            tile_w = max(w * 3, 1200)
            tile_start = scroll_l - span_cs * 0.75
            pm = QPixmap(tile_w, wave_h)
            pm.fill(QColor(15, 17, 22))
            p = QPainter(pm)
            p.setPen(QColor(45, 50, 60))
            p.drawLine(0, int(wave_h * 0.5), tile_w, int(wave_h * 0.5))
            if self.audio.main_waveform is not None:
                self._draw_wave_layer_at(p, self.audio.main_waveform, tile_w, 0, wave_h, tile_start, QColor(85, 130, 195, 165))
            if self.audio.vocal_waveform is not None:
                self._draw_wave_layer_at(p, self.audio.vocal_waveform, tile_w, 0, wave_h, tile_start, QColor(65, 225, 200, 210))
            p.end()
            self._tile_pm = pm
            self._tile_start_cs = tile_start
            self._tile_span_cs = tile_w / max(1e-6, self.px_per_cs)
            self._tile_key = key
        return self._tile_pm

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        ruler_h = 20
        syl_h = 26
        wave_top = ruler_h
        wave_h = max(30, h - ruler_h - syl_h - 4)
        syl_top = wave_top + wave_h + 2

        painter.fillRect(0, 0, w, h, QColor(15, 17, 22))

        # Ruler
        painter.fillRect(0, 0, w, ruler_h, QColor(33, 37, 44))
        painter.setPen(QColor(70, 75, 86))
        painter.drawLine(0, ruler_h - 1, w, ruler_h - 1)
        painter.setFont(QFont("Segoe UI", 8))

        scroll_l = self._scroll_left_cs()
        span_cs = w / max(1e-6, self.px_per_cs)
        scroll_r = scroll_l + span_cs
        steps = (25, 50, 100, 200, 500)
        step_cs = next((s for s in steps if s * self.px_per_cs >= 78.0), steps[-1])
        first_t = max(0, int(scroll_l // step_cs) * step_cs)

        # Cached waveform tile blit (zero numpy work per frame while scrolling!)
        if self.audio.main_waveform is not None or self.audio.vocal_waveform is not None:
            tile = self._ensure_wave_tile(w, wave_h, scroll_l, span_cs)
            if tile is not None:
                src_x = max(0, min(tile.width() - w, int(round((scroll_l - self._tile_start_cs) * self.px_per_cs))))
                painter.drawPixmap(0, wave_top, tile, src_x, 0, w, wave_h)
        else:
            painter.setPen(QColor(45, 50, 60))
            painter.drawLine(0, wave_top + int(wave_h * 0.5), w, wave_top + int(wave_h * 0.5))
            painter.setPen(QColor(120, 128, 140))
            painter.drawText(
                QRectF(0, wave_top, w, wave_h),
                Qt.AlignmentFlag.AlignCenter,
                "Load Main Audio or Vocal Stem below to see the live lookahead waveform",
            )

        for t in range(first_t, int(scroll_r) + step_cs, step_cs):
            x = int(self._cs_to_x(t))
            if 0 <= x <= w:
                painter.setPen(QColor(38, 42, 50))
                painter.drawLine(x, ruler_h, x, h)
                painter.setPen(QColor(110, 118, 132))
                painter.drawLine(x, ruler_h - 6, x, ruler_h - 1)
                sec = t / 100.0
                lbl = f"{int(sec // 60)}:{sec % 60:05.2f}"
                painter.setPen(QColor(185, 192, 205))
                painter.drawText(x + 3, 0, 68, ruler_h - 2, Qt.AlignmentFlag.AlignVCenter, lbl)

        # Live tapped syllable blocks underneath the waveform (zero list allocations per frame)
        painter.fillRect(0, syl_top, w, syl_h, QColor(22, 25, 32))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        ph_int = int(round(self.playhead_cs))
        for page in self.draft.pages:
            for line in page.lines:
                for syl in line.syllables:
                    if syl.start_cs is None:
                        continue
                    s_cs = syl.start_cs
                    e_cs = syl.end_cs if syl.end_cs is not None else max(s_cs + 8, ph_int)
                    if e_cs < scroll_l - 20 or s_cs > scroll_r + 20:
                        continue
                    x0 = self._cs_to_x(s_cs)
                    x1 = self._cs_to_x(e_cs)
                    bw = max(5.0, x1 - x0)
                    rect = QRectF(x0, float(syl_top + 2), bw, float(syl_h - 4))
                    is_open = syl.end_cs is None
                    painter.fillRect(rect, QColor(245, 152, 42) if is_open else QColor(58, 126, 192))
                    painter.setPen(QPen(QColor(255, 255, 255) if is_open else QColor(140, 190, 235), 1))
                    painter.drawRect(rect)
                    painter.setPen(QColor(15, 18, 24) if is_open else QColor(240, 245, 252))
                    painter.drawText(rect.adjusted(2, 0, -2, 0), Qt.AlignmentFlag.AlignCenter, syl.text.strip() or "␣")

        # Playhead line
        ph_x = self._cs_to_x(self.playhead_cs)
        painter.setPen(QPen(QColor(255, 70, 90), 2.0))
        painter.drawLine(QLineF(ph_x, 0.0, ph_x, float(h)))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Freeze the viewport during the drag so the red playhead tracks 1:1 under the cursor
            self._drag_scroll_left_cs = self._scroll_left_cs()
            self._dragging = True
            cs = self._x_to_cs(event.position().x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            cs = self._x_to_cs(event.position().x())
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self._drag_scroll_left_cs = None
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if delta > 0 else 1.0 / 1.2
            self.px_per_cs = max(0.5, min(10.0, self.px_per_cs * factor))
            self.update()
        else:
            step_cs = -delta / max(0.2, self.px_per_cs) * 0.35
            cs = self._clamp_cs(self.playhead_cs + step_cs)
            self.playhead_cs = cs
            self.playhead_scrubbed.emit(cs)
            self.update()


class TapSyncView(QWidget):
    """
    Modular Tap-to-Sync tab (placed before Lyrics Wiping).
    Supports KBS-style Option A tapping, rolling cross-page teleprompter, live lookahead
    waveform, stop/resume partial sync, single-line punch-in re-tap, JSON draft save/load,
    unsynced .kbp import, optional template .kbp style copy, and one-click Build .kbp.
    """

    playhead_scrubbed = Signal(float)
    project_built = Signal(object, str)  # (KbpProject, vocal_path)
    status_message = Signal(str, int)
    main_audio_status_changed = Signal(bool, str)  # (loaded, raw_path)
    dirty_changed = Signal(bool)

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.draft: TapSyncDraft = TapSyncDraft()
        self._item_by_coord: Dict[Tuple[int, int], QTreeWidgetItem] = {}

        self._is_syncing: bool = False
        self._dirty: bool = False
        self._locked: bool = False
        self._lock_reason: str = ""
        self._has_synced_project: bool = False
        self._cursor: Optional[Tuple[int, int, int]] = None
        self._open_coord: Optional[Tuple[int, int, int]] = None
        self._last_released_coord: Optional[Tuple[int, int, int]] = None
        self._space_held_coord: Optional[Tuple[int, int, int]] = None
        self._space_press_cs: int = 0
        self._punch_line: Optional[Tuple[int, int]] = None
        self._pending_punch_line: Optional[Tuple[int, int]] = None
        self._pending_seek_cs: Optional[float] = None
        self._draft_file_path: Optional[Path] = None
        self._settings = QSettings("MattJoy", "AdvancedKBPEditor")

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        # Open fresh on startup (apply saved timing preferences & default style template, but do NOT auto-load last draft)
        self._apply_saved_defaults_to_draft(self.draft)
        self._sync_timing_widgets_from_draft()
        self._refresh_template_btn()
        self._refresh_resume_btn()

    def _apply_saved_defaults_to_draft(self, draft: TapSyncDraft) -> None:
        draft.latency_enabled = self._settings.value("tap_latency_enabled", True, type=bool)
        draft.latency_cs = self._settings.value("tap_latency_cs", 4, type=int)
        draft.pre_roll_cs = self._settings.value("tap_pre_roll_cs", 150, type=int)
        saved_tpl = self._settings.value("default_template_kbp", "", type=str)
        if saved_tpl and Path(saved_tpl).exists():
            draft.template_kbp_path = saved_tpl

    def _sync_timing_widgets_from_draft(self) -> None:
        self._latency_cb.blockSignals(True)
        self._latency_cb.setChecked(self.draft.latency_enabled)
        self._latency_cb.blockSignals(False)
        self._latency_spin.blockSignals(True)
        self._latency_spin.setValue(max(0, min(15, self.draft.latency_cs)))
        self._latency_spin.blockSignals(False)
        self._preroll_spin.blockSignals(True)
        self._preroll_spin.setValue(max(0.0, min(5.0, self.draft.pre_roll_cs / 100.0)))
        self._preroll_spin.blockSignals(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        ribbon = QFrame(self)
        ribbon.setObjectName("tapSyncRibbon")
        ribbon.setStyleSheet(
            "QFrame#tapSyncRibbon { background-color: #20242c; border: 1px solid #323846; border-radius: 4px; }"
            "QPushButton { padding: 2px 8px; font-size: 11px; }"
            "QCheckBox, QLabel { font-size: 11px; color: #cbd5e1; }"
        )
        rl = QHBoxLayout(ribbon)
        rl.setContentsMargins(8, 5, 8, 5)
        rl.setSpacing(10)

        def _div() -> QFrame:
            f = QFrame(ribbon)
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFrameShadow(QFrame.Shadow.Plain)
            f.setStyleSheet("color: #3a4150;")
            return f

        def _make_section(title: str) -> Tuple[QVBoxLayout, QHBoxLayout, QHBoxLayout]:
            sec = QVBoxLayout()
            sec.setContentsMargins(0, 0, 0, 0)
            sec.setSpacing(3)
            lbl = QLabel(title, ribbon)
            lbl.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            r1 = QHBoxLayout()
            r1.setContentsMargins(0, 0, 0, 0)
            r1.setSpacing(4)
            r2 = QHBoxLayout()
            r2.setContentsMargins(0, 0, 0, 0)
            r2.setSpacing(4)
            sec.addWidget(lbl)
            sec.addLayout(r1)
            sec.addLayout(r2)
            return sec, r1, r2

        # Section 1: Lyrics & Draft
        sec_lyr, lyr_r1, lyr_r2 = _make_section("Lyrics & Draft")
        self._btn_new = QPushButton("New", ribbon)
        self._btn_new.setToolTip("Start a fresh blank Tap Sync draft")
        self._btn_new.clicked.connect(self.reset_fresh)
        lyr_r1.addWidget(self._btn_new)

        self._btn_paste = QPushButton("Paste / Edit Lyrics…", ribbon)
        self._btn_paste.setToolTip("Paste or edit slash-delimited lyrics (blank lines = page breaks)")
        self._btn_paste.clicked.connect(self._open_lyrics_editor)
        lyr_r1.addWidget(self._btn_paste)

        self._btn_load_lyr = QPushButton("Load .txt / .kbp…", ribbon)
        self._btn_load_lyr.setToolTip("Load a lyric .txt file or an unsynced LYRICSV2 .kbp file")
        self._btn_load_lyr.clicked.connect(self._load_lyrics_or_kbp_dialog)
        lyr_r1.addWidget(self._btn_load_lyr)

        self._btn_load_draft = QPushButton("Load Draft…", ribbon)
        self._btn_load_draft.setToolTip("Load a saved partial-sync JSON draft")
        self._btn_load_draft.clicked.connect(self._load_draft_dialog)
        lyr_r2.addWidget(self._btn_load_draft)

        self._btn_save_draft = QPushButton("Save Draft…", ribbon)
        self._btn_save_draft.setToolTip("Save current partial sync + audio paths to a JSON draft")
        self._btn_save_draft.clicked.connect(self._save_draft_dialog)
        lyr_r2.addWidget(self._btn_save_draft)

        self._resume_btn = QPushButton("Projects…", ribbon)
        self._resume_btn.setToolTip("Open Project Organizer — resume saved drafts, clean up old drafts, or export/import .zip bundles")
        self._resume_btn.clicked.connect(self.open_project_organizer)
        lyr_r2.addWidget(self._resume_btn)

        rl.addLayout(sec_lyr)
        rl.addWidget(_div())

        # Section 2: Tap Controls
        sec_tap, tap_r1, tap_r2 = _make_section("Tap Controls")
        self._sync_btn = QPushButton("● Start Sync", ribbon)
        self._sync_btn.setStyleSheet("background-color: #15803d; color: white; font-weight: bold;")
        self._sync_btn.setToolTip("Start or resume tap-syncing from the current cursor (with pre-roll)")
        self._sync_btn.clicked.connect(self.start_syncing)
        tap_r1.addWidget(self._sync_btn)

        self._pause_btn = QPushButton("⏸ Pause (Esc)", ribbon)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setToolTip("Pause syncing and keep all partial sync progress (Esc)")
        self._pause_btn.clicked.connect(self.stop_syncing)
        tap_r1.addWidget(self._pause_btn)

        self._btn_back_line = QPushButton("◀ Back Line (←)", ribbon)
        self._btn_back_line.setToolTip(
            "Restart current line (or step back to previous line if pressed again), erasing subsequent syncs and rewinding to the previous line's end"
        )
        self._btn_back_line.clicked.connect(self.restart_or_back_line)
        tap_r1.addWidget(self._btn_back_line)

        self._btn_retap = QPushButton("Re-tap Line (R)", ribbon)
        self._btn_retap.setToolTip("Clear only the selected line, rewind with pre-roll, and re-tap just that line")
        self._btn_retap.clicked.connect(self.start_retap_selected_line)
        tap_r1.addWidget(self._btn_retap)

        self._btn_end_phrase = QPushButton("End Phrase (Ctrl)", ribbon)
        self._btn_end_phrase.setToolTip("Mark end of held note / pause mid-line without starting the next syllable")
        self._btn_end_phrase.clicked.connect(self._end_current_phrase)
        tap_r2.addWidget(self._btn_end_phrase)

        self._btn_undo_tap = QPushButton("Undo Tap (Z)", ribbon)
        self._btn_undo_tap.setToolTip("Step back one syllable (Z or Backspace)")
        self._btn_undo_tap.clicked.connect(self._undo_last_tap)
        tap_r2.addWidget(self._btn_undo_tap)

        self._btn_clear_line = QPushButton("Clear Line", ribbon)
        self._btn_clear_line.setToolTip("Clear timestamps on the selected line")
        self._btn_clear_line.clicked.connect(self._clear_selected_line)
        tap_r2.addWidget(self._btn_clear_line)

        self._btn_clear_all = QPushButton("Clear All", ribbon)
        self._btn_clear_all.setToolTip("Clear all synced timestamps in the entire song to start over")
        self._btn_clear_all.clicked.connect(self._clear_all_syncs)
        tap_r2.addWidget(self._btn_clear_all)

        rl.addLayout(sec_tap)
        rl.addWidget(_div())

        # Section 3: Timing
        sec_time, time_r1, time_r2 = _make_section("Timing")
        self._latency_cb = QCheckBox("Latency Nudge:", ribbon)
        self._latency_cb.setChecked(True)
        self._latency_cb.setToolTip("Subtract human reaction latency from every tap")
        self._latency_cb.toggled.connect(self._on_settings_changed)
        time_r1.addWidget(self._latency_cb)

        self._latency_spin = QSpinBox(ribbon)
        self._latency_spin.setRange(0, 15)
        self._latency_spin.setValue(4)
        self._latency_spin.setSuffix(" cs")
        self._latency_spin.setFixedWidth(56)
        self._latency_spin.valueChanged.connect(self._on_settings_changed)
        time_r1.addWidget(self._latency_spin)

        time_r1.addWidget(QLabel("Pre-roll:", ribbon))
        self._preroll_spin = QDoubleSpinBox(ribbon)
        self._preroll_spin.setRange(0.0, 5.0)
        self._preroll_spin.setSingleStep(0.5)
        self._preroll_spin.setValue(1.5)
        self._preroll_spin.setSuffix(" s")
        self._preroll_spin.setFixedWidth(60)
        self._preroll_spin.valueChanged.connect(self._on_settings_changed)
        time_r1.addWidget(self._preroll_spin)

        self._play_on_retap_cb = QCheckBox("Play immediately on re-tap", ribbon)
        saved_play_retap = self._settings.value("tap_play_on_retap", True, type=bool)
        self._play_on_retap_cb.setChecked(saved_play_retap)
        self._play_on_retap_cb.setToolTip(
            "When checked: Back Line (←) and Re-tap Line (R) start playback immediately.\n"
            "When unchecked: cues the playhead and pauses so you can catch your breath and press Space when ready."
        )
        self._play_on_retap_cb.toggled.connect(self._on_play_on_retap_toggled)
        time_r2.addWidget(self._play_on_retap_cb)

        rl.addLayout(sec_time)
        rl.addWidget(_div())

        # Section 4: Build .kbp
        sec_build, bld_r1, bld_r2 = _make_section("Build .kbp")
        self._tpl_btn = QPushButton("Extract Style Template from .kbp…", ribbon)
        self._tpl_btn.setToolTip("Pick an existing .kbp file to copy its fonts, colors, and styles (remembered for future builds)")
        self._tpl_btn.clicked.connect(self._pick_template_kbp)
        bld_r1.addWidget(self._tpl_btn)

        self._build_btn = QPushButton("Build .kbp →", ribbon)
        self._build_btn.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 2px 14px;")
        self._build_btn.setToolTip("Build a .kbp project from your tapped lyrics and open it in Lyrics Wiping")
        self._build_btn.clicked.connect(self._build_kbp)
        bld_r2.addWidget(self._build_btn)

        rl.addLayout(sec_build)
        rl.addStretch()

        # Keep toolbar buttons from stealing Space/Enter focus while tapping
        for b in ribbon.findChildren(QPushButton):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self._line_tree = QTreeWidget(splitter)
        self._line_tree.setHeaderHidden(True)
        self._line_tree.setRootIsDecorated(False)
        self._line_tree.setIndentation(10)
        self._line_tree.setMinimumWidth(220)
        self._line_tree.setMaximumWidth(360)
        self._line_tree.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._line_tree.setStyleSheet(
            "QTreeWidget { background-color: #191c22; border: 1px solid #2e3440; font-size: 12px; }"
            "QTreeWidget::item { padding: 3px 4px; }"
            "QTreeWidget::item:selected { background-color: #2b5b84; color: #ffffff; }"
        )
        self._line_tree.itemClicked.connect(self._on_tree_clicked)

        right_box = QWidget(splitter)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self._teleprompter = _RollingTeleprompterWidget(right_box)
        self._teleprompter.end_phrase_requested.connect(self._end_current_phrase)
        self._waveform = _TapWaveformStrip(self.audio, right_box)
        self._waveform.playhead_scrubbed.connect(self.playhead_scrubbed.emit)

        right_layout.addWidget(self._teleprompter, 1)
        right_layout.addWidget(self._waveform)

        splitter.addWidget(self._line_tree)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(1, 1)

        # Lock / Freeze banner (shown when Tap Sync is frozen after save/import, or unlocked while editing a synced KBP)
        self._lock_banner = QFrame(self)
        self._lock_banner.setObjectName("tapSyncLockBanner")
        lb_layout = QHBoxLayout(self._lock_banner)
        lb_layout.setContentsMargins(10, 5, 10, 5)
        lb_layout.setSpacing(10)
        self._lock_label = QLabel("", self._lock_banner)
        self._lock_label.setWordWrap(True)
        lb_layout.addWidget(self._lock_label, 1)
        self._lock_toggle_btn = QPushButton("🔓 Unfreeze Tap Sync", self._lock_banner)
        self._lock_toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._lock_toggle_btn.clicked.connect(self._toggle_lock)
        lb_layout.addWidget(self._lock_toggle_btn)
        self._lock_banner.setVisible(False)

        root.addWidget(ribbon)
        root.addWidget(self._lock_banner)
        root.addWidget(splitter, 1)

    @property
    def is_syncing(self) -> bool:
        return self._is_syncing

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _set_dirty(self, dirty: bool) -> None:
        val = bool(dirty)
        if self._dirty != val:
            self._dirty = val
            self.dirty_changed.emit(val)

    def mark_clean(self) -> None:
        self._set_dirty(False)

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def has_synced_project(self) -> bool:
        return self._has_synced_project

    def set_synced_project_active(self, active: bool) -> None:
        self._has_synced_project = bool(active)
        self._update_lock_ui()

    def set_locked(self, locked: bool, reason: str = "") -> None:
        if locked and self._is_syncing:
            self.stop_syncing()
        self._locked = bool(locked)
        if reason:
            self._lock_reason = reason
        elif not locked:
            self._lock_reason = ""
        self._update_lock_ui()
        self._sync_visuals()

    def _toggle_lock(self) -> None:
        if self._locked:
            self.set_locked(False)
            self.status_message.emit(
                "Tap Sync unfrozen — select any line to re-tap, then click 'Apply to .kbp →' when done",
                5000,
            )
            self.setFocus()
        else:
            self.set_locked(True, reason="Locked")
            self.status_message.emit("Tap Sync locked", 3000)

    def _update_lock_ui(self) -> None:
        locked = self._locked
        has_synced = self._has_synced_project

        # Buttons that replace the current project with a different/unsynced draft are blocked whenever
        # Tap Sync is locked OR a synced project is already open in this window.
        allow_new_draft = (not locked) and (not has_synced)
        blocked_tip = (
            "A synced .kbp project is open in this window. "
            "Use File → New Project to clear this window, or File → New Window for another song."
        )
        self._btn_new.setEnabled(allow_new_draft)
        self._btn_new.setToolTip(blocked_tip if has_synced else "Start a fresh blank Tap Sync draft")
        self._btn_load_lyr.setEnabled(allow_new_draft)
        self._btn_load_lyr.setToolTip(
            blocked_tip if has_synced else "Load a lyric .txt file or an unsynced LYRICSV2 .kbp file"
        )
        self._btn_load_draft.setEnabled(allow_new_draft)
        self._btn_load_draft.setToolTip(
            blocked_tip if has_synced else "Load a saved partial-sync JSON draft"
        )
        self._resume_btn.setEnabled(allow_new_draft)
        self._resume_btn.setToolTip(
            blocked_tip
            if has_synced
            else "Open Project Organizer — resume saved drafts, clean up old drafts, or export/import .zip bundles"
        )

        # Editing / tapping / building controls are enabled whenever unfrozen
        can_edit = not locked
        self._btn_paste.setEnabled(can_edit)
        self._btn_save_draft.setEnabled(can_edit)
        self._sync_btn.setEnabled(can_edit and not self._is_syncing)
        self._pause_btn.setEnabled(can_edit and self._is_syncing)
        self._btn_back_line.setEnabled(can_edit)
        self._btn_retap.setEnabled(can_edit)
        self._btn_end_phrase.setEnabled(can_edit)
        self._btn_undo_tap.setEnabled(can_edit)
        self._btn_clear_line.setEnabled(can_edit)
        self._btn_clear_all.setEnabled(can_edit)
        self._tpl_btn.setEnabled(can_edit)
        self._build_btn.setEnabled(can_edit)

        if has_synced:
            self._build_btn.setText("Apply to .kbp →")
            self._build_btn.setToolTip("Apply your re-tapped timings back to the open .kbp project in Lyrics Wiping")
        else:
            self._build_btn.setText("Build .kbp →")
            self._build_btn.setToolTip("Build a .kbp project from your tapped lyrics and open it in Lyrics Wiping")

        if locked:
            reason_txt = f" ({self._lock_reason})" if self._lock_reason else ""
            self._lock_banner.setStyleSheet(
                "QFrame#tapSyncLockBanner { background-color: #2c2416; border: 1px solid #92600e; border-radius: 4px; }"
                "QLabel { color: #fde68a; font-size: 11px; }"
            )
            self._lock_label.setText(
                f"<b>🔒 Tap Sync is frozen{reason_txt}.</b>  "
                "You can click lines, scrub the waveform, and press Space to play/pause. "
                "Click <b>Unfreeze Tap Sync</b> to re-tap or modify timings."
            )
            self._lock_toggle_btn.setText("🔓 Unfreeze Tap Sync")
            self._lock_toggle_btn.setStyleSheet(
                "background-color: #b45309; color: white; font-weight: bold; padding: 3px 12px;"
            )
            self._lock_banner.setVisible(True)
        elif has_synced:
            self._lock_banner.setStyleSheet(
                "QFrame#tapSyncLockBanner { background-color: #172536; border: 1px solid #2563eb; border-radius: 4px; }"
                "QLabel { color: #bfdbfe; font-size: 11px; }"
            )
            self._lock_label.setText(
                "<b>🔓 Tap Sync unlocked for editing.</b>  "
                "Re-tap any line (R) or section, then click <b>Apply to .kbp →</b> to update Lyrics Wiping &amp; Display."
            )
            self._lock_toggle_btn.setText("🔒 Re-lock Tap Sync")
            self._lock_toggle_btn.setStyleSheet(
                "background-color: #334155; color: #e2e8f0; font-weight: bold; padding: 3px 12px;"
            )
            self._lock_banner.setVisible(True)
        else:
            self._lock_banner.setVisible(False)

    def set_playhead_cs(self, cs: float) -> None:
        self._waveform.playhead_cs = cs
        self._waveform.update()

    def viewport_window_cs(self) -> Tuple[float, float]:
        left_cs = max(0.0, self._waveform._scroll_left_cs())
        span_cs = max(50.0, self._waveform.width() / max(1e-6, self._waveform.px_per_cs))
        return (left_cs, span_cs)

    def zoom(self, factor: float) -> None:
        self._waveform.px_per_cs = max(0.5, min(10.0, self._waveform.px_per_cs * factor))
        self._waveform.invalidate_cache()
        self._waveform.update()

    def reset_zoom(self) -> None:
        self._waveform.px_per_cs = 2.4
        self._waveform.invalidate_cache()
        self._waveform.update()

    def on_waveform_loaded(self) -> None:
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)
        self._waveform.invalidate_cache()
        self._waveform.update()

    def load_synced_project(
        self,
        proj: KbpProject,
        audio_path: str = "",
        vocal_path: str = "",
        locked: bool = True,
        reason: str = "Synced .kbp imported",
    ) -> None:
        """Populate Tap Sync from an open synced KbpProject and freeze it by default."""
        draft = TapSyncDraft.from_kbp_project(proj, audio_path=audio_path, vocal_path=vocal_path)
        draft.latency_enabled = self._settings.value("tap_latency_enabled", True, type=bool)
        draft.latency_cs = self._settings.value("tap_latency_cs", 4, type=int)
        draft.pre_roll_cs = self._settings.value("tap_pre_roll_cs", 150, type=int)
        self._draft_file_path = None
        self._has_synced_project = True
        self._apply_draft(draft, load_audio=False)
        self._set_dirty(False)
        self.set_locked(locked, reason=reason)

    def load_unsynced_kbp(self, path: str | Path) -> bool:
        try:
            draft = TapSyncDraft.from_kbp_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Could not parse KBP file:\n{exc}")
            return False
        self._has_synced_project = False
        self._locked = False
        self._lock_reason = ""
        audio_ok = self._apply_draft(draft, load_audio=True)
        self._set_dirty(False)
        self._update_lock_ui()
        msg = f"Loaded lyrics from {Path(path).name} ({draft.total_syllables()} syllables)"
        if audio_ok and draft.audio_path:
            msg += f" — main audio: {Path(draft.audio_path).name}"
        elif draft.audio_path:
            msg += " — main audio missing/unreadable (click 'Load Main Audio…')"
        self.status_message.emit(msg, 6000)
        return True

    def archive_current_draft(self) -> Optional[Path]:
        """Save the current Tap Sync draft in the background archive so it can be reopened later."""
        if not self.draft.pages:
            return None
        # Don't clutter the unsynced drafts organizer with untouched synced .kbp imports
        if self._has_synced_project and self._locked:
            return None
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)
        target = self._draft_file_path or (default_drafts_dir() / self.draft.default_archive_filename())
        try:
            saved = self.draft.save_json(target)
            self._draft_file_path = saved
            return saved
        except Exception:
            return None

    def reset_fresh(self) -> None:
        """Archive the outgoing draft in the background, then start a clean empty draft."""
        self.archive_current_draft()
        fresh = TapSyncDraft()
        self._apply_saved_defaults_to_draft(fresh)
        self._draft_file_path = None
        self._has_synced_project = False
        self._locked = False
        self._lock_reason = ""
        self._apply_draft(fresh, load_audio=False)
        self._set_dirty(False)
        self._update_lock_ui()

    def _refresh_resume_btn(self) -> None:
        pass

    def open_project_organizer(self) -> None:
        if self._locked or self._has_synced_project:
            return
        self.archive_current_draft()
        dlg = ProjectOrganizerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_draft_path is not None:
            self.load_draft_file(dlg.selected_draft_path)

    def _autosave(self) -> None:
        self.archive_current_draft()

    def _apply_draft(self, draft: TapSyncDraft, load_audio: bool = True) -> bool:
        if self._is_syncing:
            self.stop_syncing()
        self.draft = draft
        self._teleprompter.draft = draft
        self._waveform.draft = draft

        self._latency_cb.blockSignals(True)
        self._latency_cb.setChecked(draft.latency_enabled)
        self._latency_cb.blockSignals(False)
        self._latency_spin.blockSignals(True)
        self._latency_spin.setValue(max(0, min(15, draft.latency_cs)))
        self._latency_spin.blockSignals(False)
        self._preroll_spin.blockSignals(True)
        self._preroll_spin.setValue(max(0.0, min(5.0, draft.pre_roll_cs / 100.0)))
        self._preroll_spin.blockSignals(False)
        self._refresh_template_btn()

        self._cursor = draft.first_unsynced_coord()
        if self._cursor is None and draft.pages and draft.pages[0].lines and draft.pages[0].lines[0].syllables:
            self._cursor = (0, 0, 0)
        self._open_coord = None
        self._last_released_coord = None
        self._punch_line = None

        main_loaded = False
        if load_audio:
            if draft.audio_path:
                main_loaded = self.audio.load_main(draft.audio_path)
            if not main_loaded:
                self.audio.clear_main()
            self.main_audio_status_changed.emit(main_loaded, draft.audio_path)

            if draft.vocal_path and Path(draft.vocal_path).exists():
                self.audio.load_vocal_stem(draft.vocal_path)

        self._rebuild_tree()
        self._sync_visuals()
        return main_loaded

    def _refresh_template_btn(self) -> None:
        if self.draft.template_kbp_path:
            name = Path(self.draft.template_kbp_path).name
            short = f"{name[:18]}…" if len(name) > 18 else name
            self._tpl_btn.setText(f"Style Template: {short}")
            self._tpl_btn.setToolTip(f"Extracting fonts, colors & styles from:\n{self.draft.template_kbp_path}\n(Click to change)")
        else:
            self._tpl_btn.setText("Extract Style Template from .kbp…")
            self._tpl_btn.setToolTip("Pick an existing .kbp file to copy its fonts, colors, and styles (remembered for future builds)")

    def _on_settings_changed(self) -> None:
        self.draft.latency_enabled = self._latency_cb.isChecked()
        self.draft.latency_cs = self._latency_spin.value()
        self.draft.pre_roll_cs = int(round(self._preroll_spin.value() * 100))
        self._settings.setValue("tap_latency_enabled", self.draft.latency_enabled)
        self._settings.setValue("tap_latency_cs", self.draft.latency_cs)
        self._settings.setValue("tap_pre_roll_cs", self.draft.pre_roll_cs)
        self._settings.sync()

    def _on_play_on_retap_toggled(self, checked: bool) -> None:
        self._settings.setValue("tap_play_on_retap", bool(checked))
        self._settings.sync()

    def _open_lyrics_editor(self) -> None:
        if self._locked:
            return
        dlg = _LyricsInputDialog(self.draft, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_draft = TapSyncDraft.from_lyrics_text(dlg.lyrics_text)
        new_draft.title = dlg.title_text
        new_draft.artist = dlg.artist_text
        new_draft.audio_path = self.draft.audio_path or (str(self.audio.main_path) if self.audio.main_path else "")
        new_draft.vocal_path = self.draft.vocal_path or (str(self.audio.vocal_path) if self.audio.vocal_path else "")
        new_draft.template_kbp_path = self.draft.template_kbp_path
        new_draft.raw_header_lines = self.draft.raw_header_lines
        new_draft.latency_enabled = self._latency_cb.isChecked()
        new_draft.latency_cs = self._latency_spin.value()
        new_draft.pre_roll_cs = int(round(self._preroll_spin.value() * 100))
        self._apply_draft(new_draft, load_audio=False)
        self._set_dirty(True)
        self._autosave()
        self.setFocus()

    def _load_lyrics_or_kbp_dialog(self) -> None:
        if self._locked or self._has_synced_project:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Lyrics or Unsynced KBP",
            "",
            "Lyrics & KBP (*.txt *.kbp);;Text Files (*.txt);;KBP Files (*.kbp);;All Files (*.*)",
        )
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() == ".kbp":
            self.load_unsynced_kbp(p)
        else:
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = p.read_text(encoding="cp1252", errors="replace")
            draft = TapSyncDraft.from_lyrics_text(text)
            draft.title = p.stem
            draft.audio_path = self.draft.audio_path or (str(self.audio.main_path) if self.audio.main_path else "")
            draft.vocal_path = self.draft.vocal_path or (str(self.audio.vocal_path) if self.audio.vocal_path else "")
            draft.template_kbp_path = self.draft.template_kbp_path
            self._apply_draft(draft, load_audio=False)
            self._set_dirty(True)
            self.status_message.emit(f"Loaded lyrics from {p.name} ({draft.total_syllables()} syllables)", 4500)
        self._autosave()
        self.setFocus()

    def save_draft(self) -> bool:
        """Save the current Tap Sync draft (to its existing file if set, otherwise prompting)."""
        if not self.draft.pages:
            return False
        if self._draft_file_path is not None:
            if self.audio.main_path:
                self.draft.audio_path = str(self.audio.main_path)
            if self.audio.vocal_path:
                self.draft.vocal_path = str(self.audio.vocal_path)
            self._draft_file_path = self.draft.save_json(self._draft_file_path)
            self._set_dirty(False)
            self.status_message.emit(f"Saved Tap Sync draft: {self._draft_file_path.name}", 4000)
            return True
        return self._save_draft_dialog()

    def _save_draft_dialog(self) -> bool:
        if not self.draft.pages:
            return False
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)
        default_dir = str(self._draft_file_path) if self._draft_file_path else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save Tap Sync Draft", default_dir, "Tap Sync Draft (*.kbsync.json);;JSON (*.json)")
        if not path:
            return False
        self._draft_file_path = self.draft.save_json(path)
        self._set_dirty(False)
        self.status_message.emit(f"Saved Tap Sync draft: {self._draft_file_path.name}", 4000)
        return True

    def _load_draft_dialog(self) -> None:
        if self._locked or self._has_synced_project:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load Tap Sync Draft", "", "Tap Sync Draft (*.kbsync.json *.json);;All Files (*.*)")
        if not path:
            return
        try:
            draft = TapSyncDraft.load_json(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Draft Failed", f"Could not read draft JSON:\n{exc}")
            return
        self._draft_file_path = Path(path)
        self._apply_draft(draft, load_audio=True)
        self._set_dirty(False)
        self.status_message.emit(
            f"Loaded draft {Path(path).name} ({draft.synced_syllables()}/{draft.total_syllables()} syllables synced)",
            4500,
        )
        self.setFocus()

    def _pick_template_kbp(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Extract Style Template from .kbp (Fonts & Colors)",
            "",
            "Karaoke Builder Projects (*.kbp);;All Files (*.*)",
        )
        if path:
            self.draft.template_kbp_path = path
            self._settings.setValue("default_template_kbp", path)
            self._settings.sync()
            self._refresh_template_btn()
            self._autosave()
            self.status_message.emit(f"Style template set from {Path(path).name}", 4000)

    def load_draft_file(self, path: str | Path) -> bool:
        try:
            draft = TapSyncDraft.load_json(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Draft Failed", f"Could not read draft JSON:\n{exc}")
            return False
        self._draft_file_path = Path(path)
        self._apply_draft(draft, load_audio=True)
        self._set_dirty(False)
        self.status_message.emit(
            f"Loaded draft {Path(path).name} ({draft.synced_syllables()}/{draft.total_syllables()} syllables synced)",
            4500,
        )
        self.setFocus()
        return True

    def _rebuild_tree(self) -> None:
        self._line_tree.blockSignals(True)
        self._line_tree.clear()
        self._item_by_coord.clear()
        hdr_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        for p_idx, page in enumerate(self.draft.pages):
            p_item = QTreeWidgetItem([f"Page {p_idx + 1}"])
            p_item.setFont(0, hdr_font)
            p_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            p_item.setBackground(0, QBrush(QColor(34, 40, 52)))
            p_item.setForeground(0, QBrush(QColor(155, 185, 225)))
            p_item.setData(0, Qt.ItemDataRole.UserRole, (p_idx, -1))
            self._line_tree.addTopLevelItem(p_item)
            for l_idx, line in enumerate(page.lines):
                child = QTreeWidgetItem([self._format_tree_line(line)])
                child.setData(0, Qt.ItemDataRole.UserRole, (p_idx, l_idx))
                self._style_tree_item(child, line)
                p_item.addChild(child)
                self._item_by_coord[(p_idx, l_idx)] = child
            p_item.setExpanded(True)
        self._line_tree.blockSignals(False)
        self._highlight_cursor_in_tree()

    @staticmethod
    def _format_tree_line(line: TapLine) -> str:
        st = line.sync_state
        dot = "●" if st == "full" else ("◐" if st == "partial" else "○")
        return f"{dot} {line.full_text.strip() or '(empty)'}"

    @staticmethod
    def _style_tree_item(item: QTreeWidgetItem, line: TapLine) -> None:
        st = line.sync_state
        if st == "full":
            item.setForeground(0, QBrush(QColor(110, 231, 183)))
        elif st == "partial":
            item.setForeground(0, QBrush(QColor(251, 191, 36)))
        else:
            item.setForeground(0, QBrush(QColor(203, 213, 225)))

    def _update_tree_line(self, p_idx: int, l_idx: int) -> None:
        item = self._item_by_coord.get((p_idx, l_idx))
        if item is not None and 0 <= p_idx < len(self.draft.pages) and 0 <= l_idx < len(self.draft.pages[p_idx].lines):
            line = self.draft.pages[p_idx].lines[l_idx]
            item.setText(0, self._format_tree_line(line))
            self._style_tree_item(item, line)

    def _highlight_cursor_in_tree(self) -> None:
        focus = self._cursor or self._open_coord
        if focus is None:
            return
        item = self._item_by_coord.get((focus[0], focus[1]))
        if item is not None:
            self._line_tree.blockSignals(True)
            self._line_tree.setCurrentItem(item)
            self._line_tree.scrollToItem(item)
            self._line_tree.blockSignals(False)

    def _on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        p_idx, l_idx = data
        if l_idx < 0:
            l_idx = 0
        if not (0 <= p_idx < len(self.draft.pages)) or not (0 <= l_idx < len(self.draft.pages[p_idx].lines)):
            return
        if self._is_syncing:
            self.stop_syncing()
        line = self.draft.pages[p_idx].lines[l_idx]
        # Place cursor on first unsynced syllable in this line (or syllable 0 if already synced)
        s_idx = next((i for i, s in enumerate(line.syllables) if not s.has_start), 0)
        self._cursor = (p_idx, l_idx, s_idx)
        self._open_coord = None
        self._punch_line = None
        if line.first_start_cs is not None:
            self.playhead_scrubbed.emit(float(max(0, line.first_start_cs - self.draft.pre_roll_cs)))
        self._sync_visuals()
        self.setFocus()

    def _sync_visuals(self) -> None:
        self._teleprompter.cursor = self._cursor
        self._teleprompter.open_coord = self._open_coord
        self._teleprompter.is_syncing = self._is_syncing
        self._teleprompter.is_locked = self._locked
        self._teleprompter.punch_line = self._punch_line
        self._teleprompter.update()
        self._waveform.update()
        self._highlight_cursor_in_tree()

    def _selected_line_coord(self) -> Optional[Tuple[int, int]]:
        cur = self._line_tree.currentItem()
        if cur is not None:
            data = cur.data(0, Qt.ItemDataRole.UserRole)
            if data and data[1] >= 0:
                return (data[0], data[1])
        if self._cursor is not None:
            return (self._cursor[0], self._cursor[1])
        return None

    def _get_syl(self, coord: Optional[Tuple[int, int, int]]) -> Optional[TapSyllable]:
        if coord is None:
            return None
        p_i, l_i, s_i = coord
        if 0 <= p_i < len(self.draft.pages):
            page = self.draft.pages[p_i]
            if 0 <= l_i < len(page.lines):
                line = page.lines[l_i]
                if 0 <= s_i < len(line.syllables):
                    return line.syllables[s_i]
        return None

    def _next_coord(self, coord: Tuple[int, int, int]) -> Optional[Tuple[int, int, int]]:
        flat = self.draft.flat_syllables()
        for i, (p_i, l_i, s_i, _) in enumerate(flat):
            if (p_i, l_i, s_i) == coord:
                if i + 1 < len(flat):
                    np_i, nl_i, ns_i, _ = flat[i + 1]
                    return (np_i, nl_i, ns_i)
                return None
        return None

    def _prev_coord(self, coord: Tuple[int, int, int]) -> Optional[Tuple[int, int, int]]:
        flat = self.draft.flat_syllables()
        for i, (p_i, l_i, s_i, _) in enumerate(flat):
            if (p_i, l_i, s_i) == coord:
                if i > 0:
                    pp_i, pl_i, ps_i, _ = flat[i - 1]
                    return (pp_i, pl_i, ps_i)
                return None
        return None

    def _effective_tap_cs(self) -> int:
        raw = int(round(self.audio.smooth_position_cs))
        offset = self.draft.latency_cs if self.draft.latency_enabled else 0
        return max(0, raw - offset)

    def _pre_roll_start_for(self, coord: Tuple[int, int, int]) -> Optional[float]:
        """Compute a comfortable pre-roll playback start position before `coord`."""
        flat = self.draft.flat_syllables()
        prev_time: Optional[int] = None
        for p_i, l_i, s_i, syl in flat:
            if (p_i, l_i, s_i) == coord:
                break
            if syl.end_cs is not None:
                prev_time = syl.end_cs
            elif syl.start_cs is not None:
                prev_time = syl.start_cs
        if prev_time is not None:
            return float(max(0, prev_time - self.draft.pre_roll_cs))
        return None

    def start_syncing(self, punch_line: Optional[Tuple[int, int]] = None, seek_cs: Optional[float] = None) -> None:
        if self._locked or not self.draft.pages:
            return
        if self._cursor is None:
            self._cursor = self.draft.first_unsynced_coord() or (0, 0, 0)
        if punch_line is None and self._pending_punch_line is not None:
            punch_line = self._pending_punch_line
        if seek_cs is None and self._pending_seek_cs is not None:
            seek_cs = self._pending_seek_cs
        self._pending_punch_line = None
        self._pending_seek_cs = None

        self._punch_line = punch_line
        self._open_coord = None
        self._last_released_coord = None
        self._space_held_coord = None
        self._is_syncing = True
        self._sync_btn.setEnabled(False)
        self._sync_btn.setText("● Syncing…")
        self._pause_btn.setEnabled(True)
        self._pause_btn.setStyleSheet("background-color: #b91c1c; color: white; font-weight: bold;")

        if seek_cs is None:
            seek_cs = self._pre_roll_start_for(self._cursor)
        if seek_cs is not None:
            # Position cleanly without scrub audio grain or duplicate debounced seek
            self.audio.set_position_cs(seek_cs, scrub_audio=False)
            self._waveform.playhead_cs = seek_cs
        self.audio.play()
        self._sync_visuals()
        self.setFocus()

    def stop_syncing(self) -> None:
        if not self._is_syncing:
            return
        # Finalize any open syllable so nothing is left half-synced
        if self._open_coord is not None:
            open_syl = self._get_syl(self._open_coord)
            if open_syl is not None and open_syl.start_cs is not None and open_syl.end_cs is None:
                now_cs = self._effective_tap_cs()
                open_syl.end_cs = max(open_syl.start_cs + 25, now_cs)
                self._update_tree_line(self._open_coord[0], self._open_coord[1])
        self._is_syncing = False
        self._open_coord = None
        self._space_held_coord = None
        self._punch_line = None
        self.audio.pause()
        self._sync_btn.setEnabled(True)
        self._sync_btn.setText("● Start Sync")
        self._sync_btn.setStyleSheet("background-color: #15803d; color: white; font-weight: bold;")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setStyleSheet("")
        self._autosave()
        self._sync_visuals()

    def toggle_syncing(self) -> None:
        if self._is_syncing:
            self.stop_syncing()
        else:
            self.start_syncing()

    def start_retap_selected_line(self) -> None:
        if self._locked:
            return
        coord = self._selected_line_coord()
        if coord is None:
            return
        p_i, l_i = coord
        line = self.draft.pages[p_i].lines[l_i]
        old_start = line.first_start_cs
        line.clear_timing()
        self._set_dirty(True)
        self._update_tree_line(p_i, l_i)
        self._cursor = (p_i, l_i, 0)

        # Prefer rewinding before the line's previous start; else before the previous synced syllable
        if old_start is not None:
            seek_cs = float(max(0, old_start - self.draft.pre_roll_cs))
        else:
            seek_cs = self._pre_roll_start_for((p_i, l_i, 0))

        if self._play_on_retap_cb.isChecked():
            self.start_syncing(punch_line=(p_i, l_i), seek_cs=seek_cs)
        else:
            if self._is_syncing:
                self.stop_syncing()
            self._pending_punch_line = (p_i, l_i)
            self._pending_seek_cs = seek_cs
            if seek_cs is not None:
                self.audio.set_position_cs(seek_cs, scrub_audio=False)
                self._waveform.playhead_cs = seek_cs
            self._autosave()
            self._sync_visuals()
            self.status_message.emit(f"Cued re-tap for P{p_i + 1} L{l_i + 1} — press Space when ready", 3500)
            self.setFocus()

    def restart_or_back_line(self) -> None:
        """
        KBS-style Back / Restart Line (Left Arrow):
        - If the current line has any tapped syllables, erases this line and all subsequent lines.
        - If already at the start of an unsynced line (or pressed repeatedly), steps back to the
          previous line and erases it and all subsequent lines.
        - Rewinds to the end of the previous synced line (minus pre-roll) and either plays
          immediately or pauses depending on 'Play immediately on re-tap'.
        """
        if self._locked:
            return
        flat_lines = self.draft.flat_lines()
        if not flat_lines:
            return

        focus = self._cursor or self._open_coord
        cur_idx = len(flat_lines) - 1
        if focus is not None:
            for i, (p_i, l_i, _) in enumerate(flat_lines):
                if (p_i, l_i) == (focus[0], focus[1]):
                    cur_idx = i
                    break
        else:
            sel = self._selected_line_coord()
            if sel is not None:
                for i, (p_i, l_i, _) in enumerate(flat_lines):
                    if (p_i, l_i) == sel:
                        cur_idx = i
                        break

        cur_line = flat_lines[cur_idx][2]
        line_has_progress = (cur_line.first_start_cs is not None) or (
            self._open_coord is not None and (self._open_coord[0], self._open_coord[1]) == (flat_lines[cur_idx][0], flat_lines[cur_idx][1])
        )
        if line_has_progress:
            target_idx = cur_idx
        else:
            target_idx = max(0, cur_idx - 1)

        # Erase target line and everything subsequent so continuous resyncing stays clean
        for i in range(target_idx, len(flat_lines)):
            p_i, l_i, ln = flat_lines[i]
            ln.clear_timing()
            self._update_tree_line(p_i, l_i)
        self._set_dirty(True)

        tp, tl, _ = flat_lines[target_idx]
        self._cursor = (tp, tl, 0)
        self._open_coord = None
        self._last_released_coord = None
        self._space_held_coord = None
        self._punch_line = None
        self._pending_punch_line = None

        # Find end of the previous synced line
        prev_end: Optional[int] = None
        for i in range(target_idx - 1, -1, -1):
            end_val = flat_lines[i][2].last_end_cs
            if end_val is not None:
                prev_end = end_val
                break
        seek_cs = float(max(0, prev_end - self.draft.pre_roll_cs)) if prev_end is not None else 0.0

        if self._play_on_retap_cb.isChecked():
            self.start_syncing(punch_line=None, seek_cs=seek_cs)
        else:
            if self._is_syncing:
                self.stop_syncing()
            self._pending_seek_cs = seek_cs
            self.audio.set_position_cs(seek_cs, scrub_audio=False)
            self._waveform.playhead_cs = seek_cs
            self._autosave()
            self._sync_visuals()
            self.status_message.emit(f"Cued P{tp + 1} L{tl + 1} from previous line end — press Space when ready", 3500)
            self.setFocus()

    def _clear_selected_line(self) -> None:
        if self._locked:
            return
        coord = self._selected_line_coord()
        if coord is None:
            return
        p_i, l_i = coord
        self.draft.pages[p_i].lines[l_i].clear_timing()
        self._set_dirty(True)
        self._update_tree_line(p_i, l_i)
        self._cursor = (p_i, l_i, 0)
        self._open_coord = None
        self._last_released_coord = None
        self._autosave()
        self._sync_visuals()

    def _clear_all_syncs(self) -> None:
        if self._locked or not self.draft.pages:
            return
        reply = QMessageBox.question(
            self,
            "Clear All Syncs",
            "Are you sure you want to clear ALL synced timestamps for this song and restart from the beginning?\n\n"
            "(Your lyrics and audio paths will be kept.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._is_syncing:
            self.stop_syncing()
        for p_i, page in enumerate(self.draft.pages):
            for l_i, line in enumerate(page.lines):
                line.clear_timing()
                self._update_tree_line(p_i, l_i)
        self._set_dirty(True)
        self._cursor = (0, 0, 0) if (self.draft.pages and self.draft.pages[0].lines and self.draft.pages[0].lines[0].syllables) else None
        self._open_coord = None
        self._last_released_coord = None
        self._punch_line = None
        self._autosave()
        self._sync_visuals()
        self.status_message.emit("Cleared all sync timestamps — ready to restart from syllable 1", 4000)
        self.setFocus()

    def _tap_next_syllable(self) -> None:
        if self._cursor is None:
            self.stop_syncing()
            return
        now_cs = self._effective_tap_cs()

        # Close previous open syllable flush to this tap (0 cs gap)
        if self._open_coord is not None:
            prev_syl = self._get_syl(self._open_coord)
            if prev_syl is not None and prev_syl.start_cs is not None:
                prev_syl.end_cs = max(prev_syl.start_cs + 1, now_cs)
                self._update_tree_line(self._open_coord[0], self._open_coord[1])
        elif self._last_released_coord is not None:
            # If user held+released Space right before immediately tapping the next word (<= 15 cs),
            # snap flush to 0 cs; if there was a real pause (> 15 cs), preserve their exact release!
            rel_syl = self._get_syl(self._last_released_coord)
            if rel_syl is not None and rel_syl.end_cs is not None and 0 < (now_cs - rel_syl.end_cs) <= 15:
                rel_syl.end_cs = now_cs
                self._update_tree_line(self._last_released_coord[0], self._last_released_coord[1])
        self._last_released_coord = None

        cur_coord = self._cursor
        cur_syl = self._get_syl(cur_coord)
        if cur_syl is None:
            self.stop_syncing()
            return

        cur_syl.start_cs = now_cs
        cur_syl.end_cs = None
        self._set_dirty(True)
        self._open_coord = cur_coord
        self._space_held_coord = cur_coord
        self._space_press_cs = now_cs
        self._update_tree_line(cur_coord[0], cur_coord[1])

        # Advance cursor (or pause at end of punch-in line until Space release / Ctrl)
        p_i, l_i, s_i = cur_coord
        line = self.draft.pages[p_i].lines[l_i]
        is_last_in_line = s_i == len(line.syllables) - 1
        if self._punch_line == (p_i, l_i) and is_last_in_line:
            self._cursor = None
        else:
            self._cursor = self._next_coord(cur_coord)
            if self._cursor is not None and self._punch_line is not None and (self._cursor[0], self._cursor[1]) != self._punch_line:
                self._cursor = None

        self._sync_visuals()

    def _end_current_phrase(self) -> None:
        if self._open_coord is None:
            return
        now_cs = self._effective_tap_cs()
        open_syl = self._get_syl(self._open_coord)
        if open_syl is not None and open_syl.start_cs is not None:
            open_syl.end_cs = max(open_syl.start_cs + 1, now_cs)
            self._set_dirty(True)
            self._update_tree_line(self._open_coord[0], self._open_coord[1])
        finished_punch = self._punch_line is not None and self._cursor is None
        finished_song = self._cursor is None
        self._open_coord = None
        self._space_held_coord = None
        if finished_punch or finished_song:
            self.stop_syncing()
            if finished_punch:
                self._cursor = self.draft.first_unsynced_coord()
                self._sync_visuals()
            elif finished_song:
                self._prompt_build_after_finish()
            return
        self._sync_visuals()

    def _prompt_build_after_finish(self) -> None:
        reply = QMessageBox.question(
            self,
            "Tap Sync Complete",
            "You've reached the last syllable! Would you like to build the .kbp file now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._build_kbp()

    def _undo_last_tap(self) -> None:
        if self._locked:
            return
        target = self._open_coord
        if target is None and self._cursor is not None:
            target = self._prev_coord(self._cursor)
        elif target is None:
            flat = self.draft.flat_syllables()
            if flat:
                p_i, l_i, s_i, _ = flat[-1]
                target = (p_i, l_i, s_i)
        if target is None:
            return
        syl = self._get_syl(target)
        if syl is not None:
            syl.clear()
            self._set_dirty(True)
            self._update_tree_line(target[0], target[1])
        self._cursor = target
        self._open_coord = None
        self._space_held_coord = None
        self._sync_visuals()

    def _build_kbp(self) -> None:
        if self._locked:
            return
        if not self.draft.pages:
            QMessageBox.information(self, "No Lyrics", "Paste or load lyrics before building a .kbp project.")
            return
        if self._is_syncing:
            self.stop_syncing()
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)

        synced = self.draft.synced_syllables()
        total = self.draft.total_syllables()
        if synced < total:
            reply = QMessageBox.question(
                self,
                "Partial Sync",
                f"{synced} of {total} syllables are synced.\n\n"
                "Build the .kbp project anyway? (Unsynced syllables will be placed sequentially so you can finish them in Lyrics Wiping.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._autosave()
        proj = self.draft.build_kbp_project()
        self._set_dirty(False)
        self.project_built.emit(proj, self.draft.vocal_path)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            event.accept()
            return
        key = event.key()
        if self._locked:
            if key == Qt.Key.Key_Space:
                self.audio.toggle_play()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Space:
            if self._is_syncing:
                self._tap_next_syllable()
            else:
                self.start_syncing()
            event.accept()
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Down) and self._is_syncing:
            self._end_current_phrase()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self.restart_or_back_line()
            event.accept()
            return
        if key == Qt.Key.Key_Backspace or (
            key == Qt.Key.Key_Z and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self._undo_last_tap()
            event.accept()
            return
        if key == Qt.Key.Key_R and not self._is_syncing:
            self.start_retap_selected_line()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._is_syncing:
            self.stop_syncing()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and self._is_syncing and self._space_held_coord is not None:
            coord = self._space_held_coord
            self._space_held_coord = None
            p_i, l_i, s_i = coord
            line = self.draft.pages[p_i].lines[l_i]
            is_last_in_line = s_i == len(line.syllables) - 1
            now_cs = self._effective_tap_cs()
            held_dur = now_cs - self._space_press_cs

            # Holding Space >= 15 cs on ANY syllable (or finishing the last syllable of a line/song)
            # closes it on release — so mid-line pauses work naturally by simply letting go of Space!
            if held_dur >= 15 or (is_last_in_line and self._cursor is None):
                syl = self._get_syl(coord)
                if syl is not None and syl.start_cs is not None:
                    syl.end_cs = max(syl.start_cs + 10, now_cs)
                    self._update_tree_line(p_i, l_i)
                self._open_coord = None
                self._last_released_coord = coord
                if self._cursor is None:
                    was_punch = self._punch_line is not None
                    self.stop_syncing()
                    if was_punch:
                        self._cursor = self.draft.first_unsynced_coord()
                        self._sync_visuals()
                    else:
                        self._prompt_build_after_finish()
                    event.accept()
                    return
                self._sync_visuals()
            event.accept()
            return
        super().keyReleaseEvent(event)
