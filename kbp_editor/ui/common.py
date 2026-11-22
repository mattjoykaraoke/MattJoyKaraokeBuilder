from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QLineF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..audio import WaveformData


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_app_icon() -> QIcon:
    """Load the application icon from assets/app_icon.ico or assets/logo.png."""
    root = repo_root()
    for rel in ("assets/app_icon.ico", "assets/logo.png"):
        p = root / rel
        if p.is_file():
            ico = QIcon(str(p))
            if not ico.isNull():
                return ico
    return QIcon()


def format_time_cs(
    cs: float,
    *,
    include_cs_suffix: bool = False,
    pad_minutes: bool = True,
    precision: int = 2,
) -> str:
    """Format centiseconds into MM:SS.xx (with optional '(N cs)' suffix)."""
    sec = max(0.0, float(cs)) / 100.0
    mins = int(sec // 60)
    rem = sec % 60
    min_str = f"{mins:02d}" if pad_minutes else f"{mins}"
    if precision <= 0:
        time_str = f"{min_str}:{int(rem):02d}"
    elif precision == 1:
        time_str = f"{min_str}:{rem:04.1f}"
    else:
        time_str = f"{min_str}:{rem:05.2f}"
    if include_cs_suffix:
        return f"{time_str} ({int(round(cs))} cs)"
    return time_str


# Distinct per-style color palette (for [A]..[F] lanes, tree items, and Display view labels)
STYLE_COLORS: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]] = {
    # style: (fill_rgb, border_rgb, label_rgb)
    "A": ((42, 86, 134), (95, 138, 185), (155, 185, 225)),
    "B": ((115, 76, 155), (180, 145, 220), (210, 175, 245)),
    "C": ((42, 126, 118), (115, 210, 200), (125, 225, 210)),
    "D": ((156, 66, 102), (235, 140, 180), (244, 150, 190)),
    "E": ((88, 128, 52), (165, 215, 115), (175, 225, 130)),
    "F": ((145, 102, 42), (230, 180, 95), (240, 195, 115)),
}

# Distinct fallback preview colors for styles B..F when not explicitly defined in the KBP header
FALLBACK_PREVIEW_WIPES: Dict[
    str,
    Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]],
] = {
    "B": ((255, 255, 255), (0, 0, 0), (195, 130, 255), (75, 35, 120)),
    "C": ((255, 255, 255), (0, 0, 0), (70, 230, 210), (20, 95, 90)),
    "D": ((255, 255, 255), (0, 0, 0), (255, 120, 175), (115, 30, 65)),
    "E": ((255, 255, 255), (0, 0, 0), (150, 235, 95), (45, 95, 20)),
    "F": ((255, 255, 255), (0, 0, 0), (255, 205, 80), (120, 75, 15)),
}


def style_palette(letter: str) -> Tuple[QColor, QColor, QColor]:
    fill_rgb, border_rgb, lbl_rgb = STYLE_COLORS.get(letter.upper(), STYLE_COLORS["B"])
    return QColor(*fill_rgb), QColor(*border_rgb), QColor(*lbl_rgb)


def style_label_color(letter: str) -> QColor:
    _, _, lbl_rgb = STYLE_COLORS.get(letter.upper(), STYLE_COLORS["B"])
    return QColor(*lbl_rgb)


def make_ribbon_divider(parent: QWidget) -> QFrame:
    f = QFrame(parent)
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFrameShadow(QFrame.Shadow.Plain)
    f.setStyleSheet("color: #3a4150;")
    return f


def make_ribbon_section(
    parent: QWidget, title: str, *, row_spacing: int = 4
) -> Tuple[QVBoxLayout, QHBoxLayout, QHBoxLayout]:
    sec = QVBoxLayout()
    sec.setContentsMargins(0, 0, 0, 0)
    sec.setSpacing(3)
    lbl = QLabel(title, parent)
    lbl.setProperty("class", "secTitle")
    lbl.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    r1 = QHBoxLayout()
    r1.setContentsMargins(0, 0, 0, 0)
    r1.setSpacing(row_spacing)
    r2 = QHBoxLayout()
    r2.setContentsMargins(0, 0, 0, 0)
    r2.setSpacing(row_spacing)
    sec.addWidget(lbl)
    sec.addLayout(r1)
    sec.addLayout(r2)
    return sec, r1, r2


def draw_waveform_layer(
    painter: QPainter,
    wf: Optional[WaveformData],
    width: int,
    top: int,
    height: int,
    scroll_left_cs: float,
    px_per_cs: float,
    color: QColor,
    *,
    step_px: int = 1,
    mid_ratio: float = 0.5,
    half_ratio: float = 0.45,
) -> None:
    """Vectorized NumPy peak-envelope bar renderer shared across all timeline and minimap widgets."""
    if wf is None or len(wf.peaks) == 0 or width <= 0 or height <= 0:
        return
    peaks = wf.peaks
    n = len(peaks)
    mid_y = top + height * mid_ratio
    half_h = height * half_ratio
    inv_px = 1.0 / max(1e-6, px_per_cs)
    scale_b = wf.buckets_per_sec / 100.0

    px_arr = np.arange(0, width, step_px, dtype=np.float64)
    t_start = scroll_left_cs + px_arr * inv_px
    t_end = scroll_left_cs + (px_arr + float(step_px)) * inv_px
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


class WaveformTileCache:
    """
    3-screen-wide cached waveform QPixmap tile so 60 fps scrolling and playhead following
    are a single fast drawPixmap sub-rect blit with zero per-frame NumPy work.
    """

    def __init__(self, *, step_px: int = 1) -> None:
        self._step_px = step_px
        self._tile_pm: Optional[QPixmap] = None
        self._tile_start_cs: float = 0.0
        self._tile_span_cs: float = 0.0
        self._tile_key: Optional[Tuple] = None

    def invalidate(self) -> None:
        self._tile_pm = None
        self._tile_key = None

    def paint(
        self,
        painter: QPainter,
        width: int,
        top: int,
        height: int,
        scroll_left_cs: float,
        px_per_cs: float,
        main_wf: Optional[WaveformData],
        vocal_wf: Optional[WaveformData],
    ) -> bool:
        """
        Blit the visible waveform slice at (0, top, width, height).
        Returns True if a waveform was drawn, or False if no waveform is loaded.
        """
        if width <= 0 or height <= 0 or (main_wf is None and vocal_wf is None):
            return False

        span_cs = width / max(1e-6, px_per_cs)
        margin_cs = 8.0 / max(1e-6, px_per_cs)
        key = (height, round(px_per_cs, 5), id(main_wf), id(vocal_wf), self._step_px)
        need_rebuild = (
            self._tile_pm is None
            or self._tile_key != key
            or (scroll_left_cs - margin_cs) < self._tile_start_cs
            or (scroll_left_cs + span_cs + margin_cs) > (self._tile_start_cs + self._tile_span_cs)
        )
        if need_rebuild:
            tile_w = max(width * 3, 1200)
            tile_start = scroll_left_cs - span_cs * 0.75
            pm = QPixmap(tile_w, height)
            pm.fill(QColor(15, 17, 22))
            p = QPainter(pm)
            p.setPen(QColor(45, 50, 60))
            p.drawLine(0, int(height * 0.5), tile_w, int(height * 0.5))
            if main_wf is not None:
                draw_waveform_layer(
                    p,
                    main_wf,
                    tile_w,
                    0,
                    height,
                    tile_start,
                    px_per_cs,
                    QColor(85, 130, 195, 165),
                    step_px=self._step_px,
                )
            if vocal_wf is not None:
                draw_waveform_layer(
                    p,
                    vocal_wf,
                    tile_w,
                    0,
                    height,
                    tile_start,
                    px_per_cs,
                    QColor(65, 225, 200, 210),
                    step_px=self._step_px,
                )
            p.end()
            self._tile_pm = pm
            self._tile_start_cs = tile_start
            self._tile_span_cs = tile_w / max(1e-6, px_per_cs)
            self._tile_key = key

        if self._tile_pm is not None:
            src_x = max(
                0,
                min(
                    self._tile_pm.width() - width,
                    int(round((scroll_left_cs - self._tile_start_cs) * px_per_cs)),
                ),
            )
            painter.drawPixmap(0, top, self._tile_pm, src_x, 0, width, height)
            return True
        return False
