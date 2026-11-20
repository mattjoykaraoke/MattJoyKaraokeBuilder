from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QPushButton, QWidget

from ..model import KbpProject, Line


LOGICAL_W = 1920.0
LOGICAL_H = 1080.0
ASPECT_16_9 = 16.0 / 9.0
RESIZE_MARGIN = 10


def _qcolor(rgb: Tuple[int, int, int]) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2])


def _format_time(cs: int) -> str:
    total_sec = cs / 100.0
    m = int(total_sec // 60)
    s = total_sec % 60
    return f"{m:02d}:{s:05.2f}"


class PreviewCanvas(QWidget):
    """
    16:9 1080p HD live karaoke preview canvas.
    Renders in a 1920x1080 logical coordinate space scaled cleanly to fill the widget.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._project: Optional[KbpProject] = None
        self._time_cs: float = 0.0
        self._use_palette_bg: bool = True
        self._show_hud: bool = True
        # Cache rendered (unwiped_pm, wiped_pm, x_start_widget, syl_widths_widget) per line
        self._line_cache: Dict[Tuple, Tuple[QPixmap, QPixmap, float, List[float]]] = {}
        self._hud_font = QFont("Consolas")
        self._hud_font.setPixelSize(28)
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def invalidate_cache(self) -> None:
        self._line_cache.clear()
        self.update()

    def set_project(self, project: Optional[KbpProject]) -> None:
        self._project = project
        self._line_cache.clear()
        self.update()

    def resizeEvent(self, event) -> None:
        self._line_cache.clear()
        super().resizeEvent(event)

    def set_time_cs(self, cs: float) -> None:
        cs_f = max(0.0, float(cs))
        if abs(cs_f - self._time_cs) > 1e-4:
            self._time_cs = cs_f
            self.update()

    def set_use_palette_bg(self, enabled: bool) -> None:
        self._use_palette_bg = enabled
        self.update()

    def _active_lines_at(self, cs: float) -> Tuple[Optional[int], List[Tuple[int, int, Line]]]:
        if not self._project:
            return None, []
        active: List[Tuple[int, int, Line]] = []
        primary_page: Optional[int] = None
        for p_idx, page in enumerate(self._project.pages):
            n_rows = len(page.lines)
            for r_idx, line in enumerate(page.lines):
                if line.display_start <= cs <= line.display_end:
                    active.append((r_idx, n_rows, line))
                    primary_page = p_idx + 1
        return primary_page, active

    def _get_line_render(
        self,
        line: Line,
        row_idx: int,
        total_rows: int,
        w: float,
        h: float,
        scale: float,
        offset_x: float,
        offset_y: float,
        dpr: float,
    ) -> Tuple[QPixmap, QPixmap, float, List[float]]:
        assert self._project is not None
        header = self._project.header
        st_letter = (line.style or "A").upper()
        style = header.get_style(st_letter)
        if st_letter in header.styles:
            # Always respect the KBP file's own defined style colors
            text_rgb = header.color(style.text_color_idx, (255, 255, 255))
            outline_rgb = header.color(style.outline_color_idx, (0, 0, 0))
            wipe_rgb = header.color(style.wipe_color_idx, (255, 140, 0))
            wipe_outline_rgb = header.color(style.wipe_outline_color_idx, (120, 40, 0))
        else:
            # Distinct fallback preview colors only for styles not defined in the KBP header
            fallback_wipes = {
                "B": ((255, 255, 255), (0, 0, 0), (195, 130, 255), (75, 35, 120)),
                "C": ((255, 255, 255), (0, 0, 0), (70, 230, 210), (20, 95, 90)),
                "D": ((255, 255, 255), (0, 0, 0), (255, 120, 175), (115, 30, 65)),
                "E": ((255, 255, 255), (0, 0, 0), (150, 235, 95), (45, 95, 20)),
                "F": ((255, 255, 255), (0, 0, 0), (255, 205, 80), (120, 75, 15)),
            }
            text_rgb, outline_rgb, wipe_rgb, wipe_outline_rgb = fallback_wipes.get(
                st_letter,
                (
                    header.color(style.text_color_idx, (255, 255, 255)),
                    header.color(style.outline_color_idx, (0, 0, 0)),
                    header.color(style.wipe_color_idx, (255, 140, 0)),
                    header.color(style.wipe_outline_color_idx, (120, 40, 0)),
                ),
            )

        syl_texts = tuple(s.text for s in line.syllables)
        key = (
            syl_texts,
            line.align,
            row_idx,
            total_rows,
            header.margins,
            style.font_name,
            style.font_size,
            style.is_bold,
            style.is_italic,
            style.uppercase,
            tuple(style.outline_widths),
            text_rgb,
            outline_rgb,
            wipe_rgb,
            wipe_outline_rgb,
            int(w),
            int(h),
            round(dpr, 2),
        )
        cached = self._line_cache.get(key)
        if cached is not None:
            return cached

        if len(self._line_cache) >= 64:
            self._line_cache.clear()

        margin_l, margin_r, _margin_t, line_spacing = header.margins
        safe_x_left = 110.0 + margin_l * 10.0
        safe_x_right = LOGICAL_W - (110.0 + margin_r * 10.0)
        usable_w = max(200.0, safe_x_right - safe_x_left)

        px_size = max(28, int(round(style.font_size * 5.2)))
        font = QFont(style.font_name or "Arial")
        font.setPixelSize(px_size)
        font.setBold(style.is_bold)
        font.setItalic(style.is_italic)

        fm = QFontMetricsF(font)
        full_text = line.full_text.upper() if style.uppercase == "U" else line.full_text
        total_text_w = fm.horizontalAdvance(full_text)

        if total_text_w > usable_w and total_text_w > 0:
            fit_scale = usable_w / total_text_w
            font.setPixelSize(max(20, int(px_size * fit_scale)))
            fm = QFontMetricsF(font)
            total_text_w = fm.horizontalAdvance(full_text)

        if line.align == "L":
            x_start = safe_x_left
        elif line.align == "R":
            x_start = safe_x_right - total_text_w
        else:
            x_start = (LOGICAL_W - total_text_w) * 0.5

        row_pitch = max(px_size * 1.45, 92.0 + line_spacing * 3.0)
        block_h = total_rows * row_pitch
        top_y = (LOGICAL_H - block_h) * 0.5
        baseline_y = top_y + row_idx * row_pitch + fm.ascent() + (row_pitch - fm.height()) * 0.5

        text_path = QPainterPath()
        text_path.addText(x_start, baseline_y, font, full_text)
        outline_w = max(2.0, sum(style.outline_widths) / 4.0 * 3.2)

        phys_w = max(1, int(round(w * dpr)))
        phys_h = max(1, int(round(h * dpr)))

        def _render_pass(fill_c: QColor, out_c: QColor) -> QPixmap:
            pm = QPixmap(phys_w, phys_h)
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.translate(offset_x, offset_y)
            p.scale(scale, scale)
            p.setClipRect(QRectF(0, 0, LOGICAL_W, LOGICAL_H))
            p.setPen(QPen(out_c, outline_w * 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(text_path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill_c)
            p.drawPath(text_path)
            p.end()
            return pm

        unwiped_pm = _render_pass(_qcolor(text_rgb), _qcolor(outline_rgb))
        wiped_pm = _render_pass(_qcolor(wipe_rgb), _qcolor(wipe_outline_rgb))

        x_start_w = offset_x + x_start * scale
        syl_widths_w: List[float] = []
        for s_txt in syl_texts:
            t_disp = s_txt.upper() if style.uppercase == "U" else s_txt
            syl_widths_w.append(fm.horizontalAdvance(t_disp) * scale)

        result = (unwiped_pm, wiped_pm, x_start_w, syl_widths_w)
        self._line_cache[key] = result
        return result

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        painter.fillRect(self.rect(), QColor(12, 12, 16))

        scale = min(w / LOGICAL_W, h / LOGICAL_H)
        canvas_w = LOGICAL_W * scale
        canvas_h = LOGICAL_H * scale
        offset_x = (w - canvas_w) * 0.5
        offset_y = (h - canvas_h) * 0.5
        dpr = self.devicePixelRatioF()

        bg_rect = QRectF(offset_x, offset_y, canvas_w, canvas_h)
        if self._project and self._use_palette_bg:
            bg_rgb = self._project.header.color(self._project.header.border_color_idx, (0, 0, 0))
            painter.fillRect(bg_rect, _qcolor(bg_rgb))
        else:
            painter.fillRect(bg_rect, QColor(14, 18, 28))

        page_num, active_lines = self._active_lines_at(self._time_cs)

        if self._project and active_lines:
            for row_idx, total_rows, line in active_lines:
                unwiped_pm, wiped_pm, x_start_w, syl_widths_w = self._get_line_render(
                    line, row_idx, total_rows, w, h, scale, offset_x, offset_y, dpr
                )
                painter.drawPixmap(0, 0, unwiped_pm)

                # Compute wipe pixel X in widget space using cached syllable widths
                wipe_x_w = x_start_w
                cur_x_w = x_start_w
                for syl, syl_w in zip(line.syllables, syl_widths_w):
                    if self._time_cs >= syl.end:
                        wipe_x_w = cur_x_w + syl_w
                    elif self._time_cs > syl.start and syl.end > syl.start:
                        frac = (self._time_cs - syl.start) / float(syl.end - syl.start)
                        wipe_x_w = max(wipe_x_w, cur_x_w + syl_w * frac)
                    cur_x_w += syl_w

                if wipe_x_w > x_start_w:
                    painter.save()
                    painter.setClipRect(QRectF(0.0, 0.0, wipe_x_w, h))
                    painter.drawPixmap(0, 0, wiped_pm)
                    painter.restore()

        if self._show_hud:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.translate(offset_x, offset_y)
            painter.scale(scale, scale)
            painter.setFont(self._hud_font)
            page_str = f"P{page_num}" if page_num else "Idle"
            hud_text = f"{_format_time(int(round(self._time_cs)))}  |  {page_str}"
            painter.fillRect(QRectF(20, 18, 410, 46), QColor(0, 0, 0, 135))
            painter.setPen(QColor(225, 232, 245))
            painter.drawText(QRectF(34, 18, 390, 46), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, hud_text)
            painter.restore()

        # Crisp thin outer border + subtle bottom-right resize grip
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawRect(0, 0, int(w) - 1, int(h) - 1)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1.5))
        painter.drawLine(int(w) - 12, int(h) - 3, int(w) - 3, int(h) - 12)
        painter.drawLine(int(w) - 7, int(h) - 3, int(w) - 3, int(h) - 7)


class FloatingPreviewWindow(QWidget):
    """
    Windowless (frameless) 16:9 1080p Live Preview overlay.
    - Drag anywhere to move freely across screens.
    - Drag any edge or corner to resize while locked to 16:9.
    - Hover-only minimal BG & Hide controls in top-right corner.
    """

    kbp_dropped = Signal(str)
    visibility_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        # Frameless Tool window owned by MainWindow: stays above the main editor
        # without using WindowStaysOnTopHint, so dialogs and other apps can rise above it.
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setWindowTitle("16:9 Live Preview")
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self.resize(512, 288)  # Exact 16:9

        self.canvas = PreviewCanvas(self)
        self.canvas.setGeometry(self.rect())

        # Hover-only mini controls
        btn_style = (
            "QPushButton { background-color: rgba(15, 18, 24, 185); color: #e2e8f0; "
            "border: 1px solid rgba(255,255,255,70); border-radius: 3px; font-size: 11px; padding: 2px 6px; }"
            "QPushButton:hover { background-color: rgba(45, 55, 72, 230); }"
        )
        self._bg_btn = QPushButton("BG", self)
        self._bg_btn.setCheckable(True)
        self._bg_btn.setChecked(True)
        self._bg_btn.setStyleSheet(btn_style)
        self._bg_btn.setToolTip("Toggle Palette BG / Dark BG")
        self._bg_btn.toggled.connect(self.canvas.set_use_palette_bg)
        self._bg_btn.hide()

        self._close_btn = QPushButton("×", self)
        self._close_btn.setStyleSheet(btn_style)
        self._close_btn.setToolTip("Hide 16:9 Preview (View → 16:9 Live Preview or F9 to show)")
        self._close_btn.clicked.connect(self.hide)
        self._close_btn.hide()

        self._drag_mode: Optional[str] = None
        self._drag_origin: QPoint = QPoint()
        self._start_geo: QRect = QRect()

    def _layout_children(self) -> None:
        self.canvas.setGeometry(self.rect())
        w = self.width()
        self._close_btn.setGeometry(w - 28, 6, 22, 22)
        self._bg_btn.setGeometry(w - 64, 6, 32, 22)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_children()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._layout_children()
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def enterEvent(self, event) -> None:
        self._bg_btn.show()
        self._close_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._drag_mode is None:
            self._bg_btn.hide()
            self._close_btn.hide()
        super().leaveEvent(event)

    def _edge_at(self, pos: QPoint) -> str:
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        left = x <= RESIZE_MARGIN
        right = x >= w - RESIZE_MARGIN
        top = y <= RESIZE_MARGIN
        bottom = y >= h - RESIZE_MARGIN
        if bottom and right:
            return "br"
        if bottom and left:
            return "bl"
        if top and right:
            return "tr"
        if top and left:
            return "tl"
        if right:
            return "r"
        if left:
            return "l"
        if bottom:
            return "b"
        if top:
            return "t"
        return "move"

    def _cursor_for_edge(self, edge: str) -> Qt.CursorShape:
        return {
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
        }.get(edge, Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = self._edge_at(event.position().toPoint())
            self._drag_origin = event.globalPosition().toPoint()
            self._start_geo = self.geometry()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode is None:
            self.setCursor(self._cursor_for_edge(self._edge_at(event.position().toPoint())))
            return

        delta = event.globalPosition().toPoint() - self._drag_origin
        g = QRect(self._start_geo)

        if self._drag_mode == "move":
            self.move(g.topLeft() + delta)
            return

        # Compute new width locked to strict 16:9 aspect ratio
        if "r" in self._drag_mode:
            new_w = max(320, g.width() + delta.x())
            new_h = max(180, int(round(new_w / ASPECT_16_9)))
            if "t" in self._drag_mode:
                self.setGeometry(g.left(), g.bottom() - new_h + 1, new_w, new_h)
            else:
                self.setGeometry(g.left(), g.top(), new_w, new_h)
        elif "l" in self._drag_mode:
            new_w = max(320, g.width() - delta.x())
            new_h = max(180, int(round(new_w / ASPECT_16_9)))
            new_left = g.right() - new_w + 1
            if "t" in self._drag_mode:
                self.setGeometry(new_left, g.bottom() - new_h + 1, new_w, new_h)
            else:
                self.setGeometry(new_left, g.top(), new_w, new_h)
        elif self._drag_mode == "b":
            new_h = max(180, g.height() + delta.y())
            new_w = max(320, int(round(new_h * ASPECT_16_9)))
            self.setGeometry(g.left(), g.top(), new_w, new_h)
        elif self._drag_mode == "t":
            new_h = max(180, g.height() - delta.y())
            new_w = max(320, int(round(new_h * ASPECT_16_9)))
            self.setGeometry(g.left(), g.bottom() - new_h + 1, new_w, new_h)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_mode = None
        if not self.rect().contains(event.position().toPoint()):
            self._bg_btn.hide()
            self._close_btn.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".kbp"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local.lower().endswith(".kbp"):
                    self.kbp_dropped.emit(local)
                    event.acceptProposedAction()
                    return
        event.ignore()
