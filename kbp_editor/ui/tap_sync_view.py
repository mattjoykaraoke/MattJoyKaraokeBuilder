from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSettings, QThread, Qt, QLineF, QRectF, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QShortcut,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from ..audio import AudioEngine
from ..local_lyric_splitter import find_local_lyric_splitter_exe, run_local_lyric_splitter
from ..model import KbpHeader, KbpProject, KbpStyle, parse_line_to_syllables, read_kbp_text, suggest_project_filename
from ..tap_sync import (
    TapLine,
    TapPage,
    TapSyllable,
    TapSyncDraft,
    default_autosaves_dir,
    default_drafts_dir,
    register_saved_project,
)
from .common import WaveformTileCache, format_time_cs, make_ribbon_divider, make_ribbon_section
from .project_organizer import ProjectOrganizerDialog


_VIDEO_LOGICAL_W = 1920.0
_VIDEO_CDG_SCALE_X = _VIDEO_LOGICAL_W / 300.0
_BORDER_NEAR_RATIO = 0.90
_BORDER_OVERFLOW_RATIO = 1.00


def _video_usable_width(header: KbpHeader) -> float:
    """Return the usable 16:9 (1920x1080) horizontal safe width matching PreviewCanvas."""
    margin_l, margin_r, _margin_t, _line_spacing = header.margins
    safe_x_left = (6.0 + margin_l) * _VIDEO_CDG_SCALE_X
    safe_x_right = _VIDEO_LOGICAL_W - (6.0 + margin_r) * _VIDEO_CDG_SCALE_X
    return max(200.0, safe_x_right - safe_x_left)


def _make_video_font(style: KbpStyle, scale: float = 1.0) -> QFont:
    """Build a QFont matching PreviewCanvas's 16:9 1080p font scaled by `scale`."""
    px_size = max(28, int(round(style.font_size * 5.2)))
    font = QFont(style.font_name or "Arial")
    font.setPixelSize(max(10, int(round(px_size * scale))))
    font.setBold(style.is_bold)
    font.setItalic(style.is_italic)
    return font


def _measure_line_16_9(text: str, header: KbpHeader, style_letter: str = "A") -> Tuple[float, float, KbpStyle]:
    """
    Measure `text` in 1920x1080 16:9 logical pixels using `header`'s style for `style_letter`.
    Returns `(line_width_px, usable_width_px, style)`.
    """
    usable_w = _video_usable_width(header)
    style = header.get_style((style_letter or "A").upper())
    font = _make_video_font(style, scale=1.0)
    fm = QFontMetricsF(font)
    disp = text.upper() if style.uppercase == "U" else text
    return fm.horizontalAdvance(disp), usable_w, style


class _LocalLyricSplitterWorker(QThread):
    """Background worker that runs Local Lyric Splitter CLI without freezing the dialog."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        exe_path: Path,
        artist: str,
        song: str,
        audio_path: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._exe_path = exe_path
        self._artist = artist
        self._song = song
        self._audio_path = audio_path
        self._process_holder: Dict[str, object] = {}
        self._cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._process_holder.get("proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            lyrics = run_local_lyric_splitter(
                self._exe_path,
                self._artist,
                self._song,
                audio_path=self._audio_path,
                process_holder=self._process_holder,
            )
            if not self._cancelled:
                self.succeeded.emit(lyrics)
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))


class _LyricsInputDialog(QDialog):
    """Modal editor to obtain (via Local Lyric Splitter) or enter/edit KBS lyrics."""

    _LLS_SETTINGS_KEY = "local_lyric_splitter_exe"
    _LLS_DOWNLOAD_URL = "https://github.com/mattjoykaraoke/LocalLyricSplitter"

    def __init__(
        self,
        draft: TapSyncDraft,
        parent: Optional[QWidget] = None,
        settings: Optional[QSettings] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Obtain / Enter Lyrics for Tap Sync")
        self.resize(640, 560)
        self._draft = draft
        self._settings = settings or QSettings("MattJoy", "MattJoyKaraokeBuilder")
        self._lls_worker: Optional[_LocalLyricSplitterWorker] = None

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Enter an <b>Artist</b> and <b>Title</b> to fetch, clean, and split lyrics with "
            "<b>Local Lyric Splitter</b>, or paste/edit lyrics below using <b>/</b> for syllable splits "
            "(e.g. <code>ev/ery/thing</code>, <code>➣➣➣/The snow</code>) and <b>blank lines</b> for page breaks:"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._artist_edit = QLineEdit(draft.artist)
        self._artist_edit.setPlaceholderText("Artist name")
        self._title_edit = QLineEdit(draft.title)
        self._title_edit.setPlaceholderText("Song title")
        form.addRow("Artist:", self._artist_edit)
        form.addRow("Title:", self._title_edit)
        layout.addLayout(form)

        lls_row = QHBoxLayout()
        self._lls_btn = QPushButton("Use Local Lyric Splitter", self)
        self._lls_btn.setStyleSheet(
            "background-color: #1d4ed8; color: white; font-weight: bold; padding: 5px 14px;"
        )
        self._lls_btn.setToolTip(
            "Automatically fetch, clean, and syllable-split lyrics using Local Lyric Splitter CLI"
        )
        self._lls_btn.clicked.connect(self._run_local_lyric_splitter)
        lls_row.addWidget(self._lls_btn)

        self._lls_status = QLabel("", self)
        self._lls_status.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self._lls_status.setWordWrap(True)
        lls_row.addWidget(self._lls_status, 1)
        layout.addLayout(lls_row)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText(
            "➣➣➣/The snow has come down\n"
            "on top of ev/ery/thing\n"
            "\n"
            "➣➣➣/True love\n"
            "The stars in the sky il/lu/mi/nate be/low"
        )
        self._text_edit.setFont(QFont("Consolas", 11))
        existing_lyrics = draft.to_lyrics_text()
        self._text_edit.setPlainText(existing_lyrics)
        layout.addWidget(self._text_edit, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        if existing_lyrics.strip():
            self._text_edit.setFocus()
        else:
            self._artist_edit.setFocus()

    def _prompt_missing_lls(self) -> str:
        """
        Inform the user Local Lyric Splitter wasn't found and return:
        'locate', 'download', or 'cancel'.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Local Lyric Splitter Not Found")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "Local Lyric Splitter was not found in the default install location.<br><br>"
            "If you have it installed elsewhere, click <b>Locate .exe…</b> to select "
            "<code>Local Lyric Splitter.exe</code>.<br><br>"
            "Need to install it? Download it from GitHub:<br>"
            f'<a href="{self._LLS_DOWNLOAD_URL}">{self._LLS_DOWNLOAD_URL}</a>'
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

    def _resolve_lls_exe(self) -> Optional[Path]:
        saved = self._settings.value(self._LLS_SETTINGS_KEY, "", type=str)
        found = find_local_lyric_splitter_exe(configured_path=saved)
        if found is not None:
            return found

        action = self._prompt_missing_lls()
        if action == "download":
            QDesktopServices.openUrl(QUrl(self._LLS_DOWNLOAD_URL))
            return None
        if action != "locate":
            return None

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate Local Lyric Splitter.exe",
            r"C:\Program Files (x86)",
            "Executable Files (*.exe);;All Files (*.*)",
        )
        if not path:
            return None
        chosen = Path(path)
        if not chosen.is_file():
            QMessageBox.warning(self, "Invalid File", f"File not found:\n{chosen}")
            return None
        self._settings.setValue(self._LLS_SETTINGS_KEY, str(chosen))
        self._settings.sync()
        return chosen

    def _set_lls_busy(self, busy: bool, status_text: str = "", status_color: str = "#94a3b8") -> None:
        self._lls_btn.setEnabled(not busy)
        self._lls_btn.setText("Fetching && Splitting…" if busy else "Use Local Lyric Splitter")
        self._title_edit.setEnabled(not busy)
        self._artist_edit.setEnabled(not busy)
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(not busy)
        self._lls_status.setStyleSheet(f"color: {status_color}; font-size: 11px;")
        self._lls_status.setText(status_text)

    def _run_local_lyric_splitter(self) -> None:
        artist = self.artist_text
        title = self.title_text
        if not artist or not title:
            QMessageBox.warning(
                self,
                "Artist & Title Required",
                "Please enter both Artist and Title before running Local Lyric Splitter.",
            )
            if not artist:
                self._artist_edit.setFocus()
            else:
                self._title_edit.setFocus()
            return

        exe_path = self._resolve_lls_exe()
        if exe_path is None:
            return

        self._set_lls_busy(True, "Running Local Lyric Splitter (fetching, cleaning & splitting)…", "#93c5fd")
        self._lls_worker = _LocalLyricSplitterWorker(
            exe_path=exe_path,
            artist=artist,
            song=title,
            audio_path=self._draft.audio_path,
            parent=self,
        )
        self._lls_worker.succeeded.connect(self._on_lls_succeeded)
        self._lls_worker.failed.connect(self._on_lls_failed)
        self._lls_worker.start()

    def _on_lls_succeeded(self, lyrics: str) -> None:
        self._text_edit.setPlainText(lyrics)
        self._set_lls_busy(
            False,
            "Lyrics fetched, cleaned & split — review below and click OK.",
            "#86efac",
        )
        self._text_edit.setFocus()

    def _on_lls_failed(self, error_msg: str) -> None:
        self._set_lls_busy(False, "Local Lyric Splitter failed.", "#fca5a5")
        QMessageBox.warning(self, "Local Lyric Splitter Failed", error_msg)

    def _stop_lls_worker(self) -> None:
        if self._lls_worker is not None and self._lls_worker.isRunning():
            self._lls_worker.cancel()
            self._lls_worker.wait(2000)

    def reject(self) -> None:
        self._stop_lls_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_lls_worker()
        super().closeEvent(event)

    @property
    def lyrics_text(self) -> str:
        return self._text_edit.toPlainText()

    @property
    def title_text(self) -> str:
        return self._title_edit.text().strip()

    @property
    def artist_text(self) -> str:
        return self._artist_edit.text().strip()


class _PageLyricsEditDialog(QDialog):
    """Focused modal editor for a single page's lyrics with live 16:9 width feedback."""

    def __init__(
        self,
        page_idx: int,
        page: TapPage,
        header: KbpHeader,
        focus_line_idx: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Page {page_idx + 1} Lyrics")
        self.resize(600, 340)
        self._header = header
        self._default_style = (
            page.lines[focus_line_idx].style
            if 0 <= focus_line_idx < len(page.lines)
            else (page.lines[0].style if page.lines else "A")
        )

        layout = QVBoxLayout(self)
        hint = QLabel(
            f"Edit lyrics for <b>Page {page_idx + 1}</b> using <b>/</b> for syllable splits "
            "(insert a <b>blank line</b> to split into multiple pages):"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._text_edit = QPlainTextEdit(self)
        self._text_edit.setFont(QFont("Consolas", 11))
        page_text = "\n".join(ln.slash_text for ln in page.lines if ln.syllables)
        self._text_edit.setPlainText(page_text)
        self._text_edit.textChanged.connect(self._update_fit_status)
        layout.addWidget(self._text_edit, 1)

        self._fit_label = QLabel("", self)
        self._fit_label.setWordWrap(True)
        self._fit_label.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self._fit_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Ctrl+Enter / Ctrl+Return submits immediately for fast page-by-page checking
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.accept)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.accept)

        # Position cursor on the line that was double-clicked
        doc = self._text_edit.document()
        block_num = max(0, min(focus_line_idx, max(0, doc.blockCount() - 1)))
        block = doc.findBlockByNumber(block_num)
        if block.isValid():
            cur = self._text_edit.textCursor()
            cur.setPosition(block.position())
            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self._text_edit.setTextCursor(cur)
        self._text_edit.setFocus()
        self._update_fit_status()

    def _update_fit_status(self) -> None:
        raw_lines = self._text_edit.toPlainText().splitlines()
        max_ratio = 0.0
        max_line_num = 0
        non_empty_count = 0
        style_used = self._header.get_style((self._default_style or "A").upper())

        for idx, raw in enumerate(raw_lines):
            stripped = raw.strip()
            if not stripped:
                continue
            non_empty_count += 1
            rendered = "".join(parse_line_to_syllables(stripped))
            line_w, usable_w, style_used = _measure_line_16_9(rendered, self._header, self._default_style)
            ratio = line_w / usable_w if usable_w > 0 else 0.0
            if ratio > max_ratio:
                max_ratio = ratio
                max_line_num = idx + 1

        font_desc = f"{style_used.font_name} {style_used.font_size}pt"
        if non_empty_count == 0:
            self._fit_label.setStyleSheet("font-size: 11px; color: #94a3b8;")
            self._fit_label.setText("Page is empty — clicking OK will remove this page.")
            return

        pct = int(round(max_ratio * 100))
        if max_ratio > _BORDER_OVERFLOW_RATIO:
            self._fit_label.setStyleSheet("font-size: 11px; color: #fca5a5; font-weight: bold;")
            self._fit_label.setText(
                f"✕ Line {max_line_num} exceeds 16:9 border ({pct}% — {font_desc}) — consider splitting"
            )
        elif max_ratio >= _BORDER_NEAR_RATIO:
            self._fit_label.setStyleSheet("font-size: 11px; color: #fcd34d; font-weight: bold;")
            self._fit_label.setText(
                f"⚠ Line {max_line_num} is close to 16:9 border ({pct}% — {font_desc})"
            )
        else:
            self._fit_label.setStyleSheet("font-size: 11px; color: #86efac;")
            self._fit_label.setText(
                f"✓ All {non_empty_count} line{'s' if non_empty_count != 1 else ''} fit 16:9 border "
                f"(widest: Line {max_line_num} at {pct}% — {font_desc})"
            )

    @property
    def lyrics_text(self) -> str:
        return self._text_edit.toPlainText()


class _RollingTeleprompterWidget(QWidget):
    """
    High-contrast rolling line teleprompter with cross-page lookahead.
    Never blindsides the user with a sudden page flip: upcoming lines from the
    next page are always visible below with a clear page-break badge.
    Also displays thin vertical 16:9 safe-area borders scaled to the active
    KBP style font so long lines can be spotted and split before syncing.
    """

    end_phrase_requested = Signal()
    line_style_cycle_requested = Signal(int, int)
    page_edit_requested = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.draft: TapSyncDraft = TapSyncDraft()
        self.cursor: Optional[Tuple[int, int, int]] = None      # next syllable to tap
        self.open_coord: Optional[Tuple[int, int, int]] = None  # currently sustaining syllable
        self.is_syncing: bool = False
        self.is_locked: bool = False
        self.duet_mode: bool = False
        self.duet_pass: int = 1
        self.duet_setup_mode: bool = False
        self.punch_line: Optional[Tuple[int, int]] = None
        self.preview_flat_idx: Optional[int] = None
        self._rendered_line_rects: List[Tuple[QRectF, int, int]] = []
        self._cached_flat_lines: Optional[List[Tuple[int, int, TapLine]]] = None
        self._cached_counts: Optional[Tuple[int, int]] = None
        self._cached_header: Optional[KbpHeader] = None
        self._cached_draft_id: int = 0
        self.setMinimumHeight(220)

    def invalidate_cache(self) -> None:
        self._cached_flat_lines = None
        self._cached_counts = None
        self._cached_header = None

    def _ensure_header(self) -> KbpHeader:
        did = id(self.draft)
        if self._cached_header is None or self._cached_draft_id != did:
            self._cached_header = self.draft.resolve_header()
        return self._cached_header

    def _ensure_draft_cache(self) -> Tuple[List[Tuple[int, int, TapLine]], int, int]:
        did = id(self.draft)
        if self._cached_flat_lines is None or self._cached_counts is None or self._cached_draft_id != did:
            self._cached_flat_lines = self.draft.flat_lines()
            self._cached_counts = (self.draft.synced_syllables(), self.draft.total_syllables())
            self._cached_header = self.draft.resolve_header()
            self._cached_draft_id = did
        return self._cached_flat_lines, self._cached_counts[0], self._cached_counts[1]

    def reset_scroll(self) -> None:
        if self.preview_flat_idx is not None:
            self.preview_flat_idx = None
            self.update()

    def _cursor_flat_index(self, flat_lines: List[Tuple[int, int, TapLine]]) -> int:
        focus_coord = self.cursor or self.open_coord
        if focus_coord is not None:
            for idx, (p_i, l_i, _) in enumerate(flat_lines):
                if (p_i, l_i) == (focus_coord[0], focus_coord[1]):
                    return idx
        return max(0, len(flat_lines) - 1)

    def wheelEvent(self, event: QWheelEvent) -> None:
        flat_lines, _, _ = self._ensure_draft_cache()
        if not flat_lines:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        step = -1 if delta > 0 else 1
        base = self.preview_flat_idx if self.preview_flat_idx is not None else self._cursor_flat_index(flat_lines)
        self.preview_flat_idx = max(0, min(len(flat_lines) - 1, base + step))
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self.is_syncing:
            self.end_phrase_requested.emit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.duet_mode and self.duet_setup_mode and not self.is_locked:
            pos = event.position()
            for rect, p_i, l_i in self._rendered_line_rects:
                if rect.contains(pos):
                    self.line_style_cycle_requested.emit(p_i, l_i)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.is_syncing and not self.is_locked:
            pos = event.position()
            for rect, p_i, l_i in self._rendered_line_rects:
                if rect.contains(pos):
                    self.page_edit_requested.emit(p_i, l_i)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(18, 21, 27))

        self._rendered_line_rects.clear()
        flat_lines, synced, total = self._ensure_draft_cache()
        if not flat_lines:
            painter.setFont(QFont("Segoe UI", 12))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Click 'Obtain / Enter Lyrics' or load a .txt / .kbp / .mjkp file (drag and drop supported).",
            )
            return

        # Find active line index in flat_lines (use wheel-scrolled preview index when browsing, including while rolling)
        cursor_flat_idx = self._cursor_flat_index(flat_lines)
        is_preview_scrolled = (
            self.preview_flat_idx is not None
            and 0 <= self.preview_flat_idx < len(flat_lines)
            and self.preview_flat_idx != cursor_flat_idx
        )
        if self.preview_flat_idx is not None and 0 <= self.preview_flat_idx < len(flat_lines):
            active_flat_idx = self.preview_flat_idx
        else:
            active_flat_idx = cursor_flat_idx

        # Top status pill (Locked / Duet Setup / Syncing / Paused phrase / Punch-in / Previewing / Ready)
        if self.is_locked:
            badge_txt = f"🔒 LOCKED ({synced}/{total} syllables synced) — Click 'Unfreeze Tap Sync' above to re-tap or modify"
            badge_bg = QColor(71, 56, 30, 225)
        elif self.duet_mode and self.duet_setup_mode:
            badge_txt = (
                "🎭 DUET SETUP — Click any line to cycle [A] → [B] → [C] (or use sidebar / keys 1·2·3), "
                "then click '✓ Ready to Sync'"
            )
            badge_bg = QColor(88, 28, 135, 220)
        elif self.punch_line is not None and self.is_syncing:
            badge_txt = f"● RE-TAPPING P{self.punch_line[0] + 1} L{self.punch_line[1] + 1}  (stops automatically at end of line)"
            badge_bg = QColor(180, 83, 9, 210)
        elif self.duet_mode:
            p_synced, p_total = self.draft.pass_syllable_counts(self.duet_pass)
            round_lbl = "ROUND 1 ([A] Singer 1 + [C] Together)" if self.duet_pass == 1 else "ROUND 2 ([B] Singer 2)"
            if self.is_syncing and self.open_coord is None and self.cursor is not None:
                badge_txt = f"⏸ {round_lbl} — Tap Space on the orange syllable ({p_synced}/{p_total} synced)"
                badge_bg = QColor(30, 64, 115, 220)
            elif self.is_syncing:
                badge_txt = f"● {round_lbl} — Tap Space on the orange syllable ({p_synced}/{p_total} synced)"
                badge_bg = QColor(22, 101, 52, 210)
            elif is_preview_scrolled:
                pv_p, pv_l, _ = flat_lines[active_flat_idx]
                badge_txt = (
                    f"PREVIEWING LINE {active_flat_idx + 1}/{len(flat_lines)} (P{pv_p + 1} L{pv_l + 1}) · "
                    f"{round_lbl} ({p_synced}/{p_total}) — Press Space to sync"
                )
                badge_bg = QColor(30, 58, 95, 220)
            else:
                badge_txt = f"🎭 {round_lbl} READY ({p_synced}/{p_total} syllables synced) — Click 'Start Sync' or press Space"
                badge_bg = QColor(45, 38, 72, 220) if self.duet_pass == 1 else QColor(28, 52, 92, 220)
        elif self.is_syncing and self.open_coord is None and self.cursor is not None:
            badge_txt = "⏸ WAITING FOR NEXT WORD — Tap Space on the orange syllable"
            badge_bg = QColor(30, 64, 115, 220)
        elif self.is_syncing:
            badge_txt = "● SYNCING — Tap Space on the orange syllable (hold & release Space, or Ctrl/↓/Right-Click for pauses)"
            badge_bg = QColor(22, 101, 52, 210)
        elif is_preview_scrolled:
            pv_p, pv_l, _ = flat_lines[active_flat_idx]
            badge_txt = (
                f"PREVIEWING LINE {active_flat_idx + 1}/{len(flat_lines)} (P{pv_p + 1} L{pv_l + 1}) — "
                "Scroll to inspect · Double-click a line to edit its page · Space to sync"
            )
            badge_bg = QColor(30, 58, 95, 220)
        else:
            badge_txt = (
                f"READY ({synced}/{total} syllables synced) — "
                "Double-click any line to edit its page · Click 'Start Sync' or press Space"
            )
            badge_bg = QColor(38, 46, 60, 210)

        bar_rect = QRectF(16.0, 10.0, float(w - 32), 26.0)
        painter.setBrush(badge_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 5.0, 5.0)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(241, 245, 249))
        painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter, badge_txt)

        # Rolling window: 1 previous line above, active line centered, up to 4 upcoming lines below
        start_idx = max(0, active_flat_idx - 1)
        end_idx = min(len(flat_lines), active_flat_idx + 5)

        top_y = 48.0
        bottom_y = float(h - 24)
        avail_h = max(120.0, bottom_y - top_y)
        active_y = top_y + avail_h * 0.30
        row_step = min(54.0, max(36.0, avail_h / 5.4))

        # Compute 16:9 safe-area vertical border geometry scaled from the active KBP style font
        header = self._ensure_header()
        usable_w_1080 = _video_usable_width(header)
        gutter = 76.0 if self.duet_mode else max(44.0, w * 0.06)
        max_guide_w = max(200.0, w - 2.0 * gutter)
        guide_scale = min(max_guide_w / usable_w_1080, 0.46)
        guide_w = usable_w_1080 * guide_scale
        guide_left_x = (w - guide_w) * 0.5
        guide_right_x = guide_left_x + guide_w

        _, _, active_line_obj = flat_lines[active_flat_idx]
        active_video_w, _, active_style = _measure_line_16_9(
            active_line_obj.full_text, header, active_line_obj.style
        )
        active_ratio = active_video_w / usable_w_1080 if usable_w_1080 > 0 else 0.0

        # Draw thin vertical 16:9 page borders spanning the lyric preview area
        if active_ratio > _BORDER_OVERFLOW_RATIO:
            guide_pen_color = QColor(239, 68, 68, 215)
            guide_active_color = QColor(248, 113, 113, 245)
            guide_lbl_color = QColor(252, 165, 165)
        elif active_ratio >= _BORDER_NEAR_RATIO:
            guide_pen_color = QColor(245, 158, 11, 185)
            guide_active_color = QColor(251, 191, 36, 235)
            guide_lbl_color = QColor(253, 230, 138)
        else:
            guide_pen_color = QColor(120, 140, 170, 88)
            guide_active_color = QColor(148, 163, 184, 150)
            guide_lbl_color = QColor(148, 163, 184, 190)

        guide_top = top_y - 4.0
        guide_bottom = float(h - 22)
        painter.setPen(QPen(guide_pen_color, 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QLineF(guide_left_x, guide_top, guide_left_x, guide_bottom))
        painter.drawLine(QLineF(guide_right_x, guide_top, guide_right_x, guide_bottom))

        # Subtle bottom caption showing the active font & 16:9 width percentage
        pct = int(round(active_ratio * 100))
        if active_ratio > _BORDER_OVERFLOW_RATIO:
            fit_suffix = f"{pct}% — Exceeds 16:9 border!"
        elif active_ratio >= _BORDER_NEAR_RATIO:
            fit_suffix = f"{pct}% — Near 16:9 border"
        else:
            fit_suffix = f"{pct}%"
        guide_caption = f"16:9 Border ({active_style.font_name} {active_style.font_size}pt · {fit_suffix})"
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(guide_lbl_color)
        painter.drawText(
            QRectF(guide_left_x, float(h - 20), guide_w, 16.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            guide_caption,
        )

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
            st_upper = (line.style or "A").upper()
            in_active_pass = (
                True
                if (not self.duet_mode or self.duet_setup_mode)
                else self.draft.is_line_in_pass(line, self.duet_pass)
            )

            row_hit_rect = QRectF(24.0, y_center - row_step * 0.44, float(w - 48), row_step * 0.88)
            self._rendered_line_rects.append((row_hit_rect, p_i, l_i))

            if is_active_line:
                painter.setBrush(QColor(30, 38, 52, 200))
                painter.setPen(QPen(QColor(71, 85, 105), 1))
                painter.drawRoundedRect(row_hit_rect, 6.0, 6.0)
                # Crisp solid vertical border ticks inside the active row so edge clearance is unmistakable
                painter.setPen(QPen(guide_active_color, 1.5))
                painter.drawLine(
                    QLineF(guide_left_x, row_hit_rect.top() + 2.0, guide_left_x, row_hit_rect.bottom() - 2.0)
                )
                painter.drawLine(
                    QLineF(guide_right_x, row_hit_rect.top() + 2.0, guide_right_x, row_hit_rect.bottom() - 2.0)
                )
            elif self.duet_mode and self.duet_setup_mode:
                painter.setBrush(QColor(24, 29, 38, 140))
                painter.setPen(QPen(QColor(48, 56, 70, 140), 1))
                painter.drawRoundedRect(row_hit_rect, 5.0, 5.0)

            # In Duet Mode, draw a clear [A] / [B] / [C] singer badge on the left of the row
            if self.duet_mode:
                pill_rect = QRectF(32.0, y_center - 10.0, 30.0, 20.0)
                if not in_active_pass:
                    p_bg = QColor(36, 41, 50, 165)
                    p_bd = QColor(74, 82, 96, 150)
                    p_fg = QColor(108, 116, 132, 185)
                elif st_upper == "B":
                    p_bg = QColor(30, 58, 138, 215)
                    p_bd = QColor(96, 165, 250)
                    p_fg = QColor(219, 234, 254)
                elif st_upper == "C":
                    p_bg = QColor(88, 28, 135, 215)
                    p_bd = QColor(192, 132, 252)
                    p_fg = QColor(243, 232, 255)
                else:
                    p_bg = QColor(127, 29, 29, 215)
                    p_bd = QColor(248, 113, 113)
                    p_fg = QColor(254, 226, 226)
                painter.setBrush(p_bg)
                painter.setPen(QPen(p_bd, 1))
                painter.drawRoundedRect(pill_rect, 4.0, 4.0)
                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                painter.setPen(p_fg)
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, f"[{st_upper}]")

            line_style = header.get_style(st_upper)
            if is_active_line:
                # Active line uses the exact KBP style font scaled 1:1 with the 16:9 vertical borders
                font = _make_video_font(line_style, scale=guide_scale)
            else:
                # Upcoming/previous lines scale down for visual hierarchy while keeping style family
                rel_scale = guide_scale * (0.68 if rel == 1 else 0.58)
                font = _make_video_font(line_style, scale=rel_scale)
            painter.setFont(font)
            fm = QFontMetricsF(font)

            use_upper = line_style.uppercase == "U"
            disp_texts = [s.text.upper() if use_upper else s.text for s in line.syllables]
            widths = [fm.horizontalAdvance(t) for t in disp_texts]
            total_w = sum(widths)
            if is_active_line:
                # Center symmetrically against the 16:9 border guides (allow overflow past guides to show!)
                x = (w - total_w) * 0.5
            else:
                min_x = 70.0 if self.duet_mode else 36.0
                x = max(min_x, (w - total_w) * 0.5)
            text_y = y_center + fm.ascent() * 0.36

            for s_idx, (syl, disp_t, sw) in enumerate(zip(line.syllables, disp_texts, widths)):
                coord = (p_i, l_i, s_idx)
                is_next = (not self.duet_setup_mode) and (coord == self.cursor)

                if is_next:
                    # Clean KBS-style active highlight: plain bright orange text, no underline or box
                    color = QColor(245, 145, 35)
                elif not in_active_pass:
                    # Inactive singer style in this round: greyed out just like already-synced words
                    color = QColor(74, 82, 96, 145 if is_past_line else 195)
                elif (not self.duet_setup_mode) and syl.has_start:
                    # Done / inactive word: dull dark-mode gray so it recedes into the background
                    color = QColor(74, 82, 96, 150 if is_past_line else 210)
                else:
                    if is_past_line and not self.duet_setup_mode:
                        color = QColor(90, 98, 112, 130)
                    elif self.duet_mode:
                        # Duet Mode syllable splits:
                        # [A] Singer 1 = Red/White, [B] Singer 2 = Blue/White, [C] Together = Purple/White
                        alpha = 255 if is_active_line else (205 if abs(rel) == 1 else 145)
                        if s_idx % 2 == 0:
                            color = QColor(244, 246, 252, alpha)
                        elif st_upper == "B":
                            color = QColor(105, 180, 255, alpha)
                        elif st_upper == "C":
                            color = QColor(198, 142, 255, alpha)
                        else:
                            color = QColor(252, 120, 120, alpha)
                    else:
                        # Solo Mode: alternating crisp white / soft sage for slash-split readability
                        alpha = 255 if is_active_line else (195 if rel == 1 else 130)
                        if s_idx % 2 == 0:
                            color = QColor(238, 242, 248, alpha)
                        else:
                            color = QColor(152, 212, 174, alpha)

                painter.setPen(color)
                painter.drawText(int(round(x)), int(round(text_y)), disp_t)
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
        self._wave_tile = WaveformTileCache(step_px=1)
        self.setMinimumHeight(110)
        self.setMaximumHeight(150)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def invalidate_cache(self) -> None:
        self._wave_tile.invalidate()

    def _max_cs(self) -> float:
        if self.audio.duration_cs > 0:
            return float(self.audio.duration_cs)
        max_syl = 0
        for page in self.draft.pages:
            for line in page.lines:
                for syl in line.syllables:
                    if syl.end_cs is not None and syl.end_cs > max_syl:
                        max_syl = syl.end_cs
                    elif syl.start_cs is not None and syl.start_cs > max_syl:
                        max_syl = syl.start_cs
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
        if not self._wave_tile.paint(
            painter,
            w,
            wave_top,
            wave_h,
            scroll_l,
            self.px_per_cs,
            self.audio.main_waveform,
            self.audio.vocal_waveform,
        ):
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
                lbl = format_time_cs(t, pad_minutes=False)
                painter.setPen(QColor(185, 192, 205))
                painter.drawText(x + 3, 0, 68, ruler_h - 2, Qt.AlignmentFlag.AlignVCenter, lbl)

        # Live tapped syllable blocks underneath the waveform (zero list allocations per frame)
        painter.fillRect(0, syl_top, w, syl_h, QColor(22, 25, 32))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        ph_int = int(round(self.playhead_cs))
        duet = self.draft.duet_mode
        for page in self.draft.pages:
            for line in page.lines:
                st_up = (line.style or "A").upper()
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
                    is_open = syl.end_cs is None
                    if duet:
                        # Split [A] top / [B] bottom on the strip so overlapping duet vocals stay legible
                        if st_up == "B":
                            rect = QRectF(x0, float(syl_top + syl_h * 0.5), bw, float(syl_h * 0.5 - 1))
                            fill_c = QColor(245, 152, 42) if is_open else QColor(48, 118, 205, 225)
                            pen_c = QColor(255, 255, 255) if is_open else QColor(147, 197, 253)
                        elif st_up == "C":
                            rect = QRectF(x0, float(syl_top + 2), bw, float(syl_h - 4))
                            fill_c = QColor(245, 152, 42) if is_open else QColor(138, 72, 208, 225)
                            pen_c = QColor(255, 255, 255) if is_open else QColor(216, 180, 254)
                        else:
                            rect = QRectF(x0, float(syl_top + 1), bw, float(syl_h * 0.5 - 1))
                            fill_c = QColor(245, 152, 42) if is_open else QColor(185, 60, 60, 225)
                            pen_c = QColor(255, 255, 255) if is_open else QColor(252, 165, 165)
                    else:
                        rect = QRectF(x0, float(syl_top + 2), bw, float(syl_h - 4))
                        fill_c = QColor(245, 152, 42) if is_open else QColor(58, 126, 192)
                        pen_c = QColor(255, 255, 255) if is_open else QColor(140, 190, 235)
                    painter.fillRect(rect, fill_c)
                    painter.setPen(QPen(pen_c, 1))
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
    history_changed = Signal()
    open_project_requested = Signal(str)  # path selected in Project Organizer
    save_project_requested = Signal()  # unified Save Project action

    def __init__(self, audio: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio = audio
        self.draft: TapSyncDraft = TapSyncDraft()
        self._item_by_coord: Dict[Tuple[int, int], QTreeWidgetItem] = {}
        self._undo_stack: List[Tuple[str, TapSyncDraft, Optional[Tuple[int, int, int]], Optional[int]]] = []
        self._redo_stack: List[Tuple[str, TapSyncDraft, Optional[Tuple[int, int, int]], Optional[int]]] = []
        self._max_history: int = 80

        self._is_syncing: bool = False
        self._duet_setup_mode: bool = False
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
        self._settings = QSettings("MattJoy", "MattJoyKaraokeBuilder")

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        # Open fresh on startup (apply saved timing preferences & default style template, but do NOT auto-load last draft)
        self._apply_saved_defaults_to_draft(self.draft)
        self._sync_timing_widgets_from_draft()
        self._refresh_template_btn()

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
            return make_ribbon_divider(ribbon)

        def _make_section(title: str) -> Tuple[QVBoxLayout, QHBoxLayout, QHBoxLayout]:
            return make_ribbon_section(ribbon, title)

        # Section 1: Lyrics & Draft
        sec_lyr, lyr_r1, lyr_r2 = _make_section("Lyrics & Draft")
        self._btn_new = QPushButton("New", ribbon)
        self._btn_new.setToolTip("Start a fresh blank Tap Sync draft")
        self._btn_new.clicked.connect(self.reset_fresh)
        lyr_r1.addWidget(self._btn_new)

        self._btn_paste = QPushButton("Obtain / Enter Lyrics…", ribbon)
        self._btn_paste.setToolTip(
            "Obtain lyrics via Local Lyric Splitter or paste/edit slash-delimited lyrics (blank lines = page breaks)"
        )
        self._btn_paste.clicked.connect(self._open_lyrics_editor)
        lyr_r1.addWidget(self._btn_paste)

        self._btn_load_lyr = QPushButton("Load File", ribbon)
        self._btn_load_lyr.setToolTip("Load a .txt, .kbp, or .mjkp file")
        self._btn_load_lyr.clicked.connect(self._load_lyrics_or_kbp_dialog)
        lyr_r1.addWidget(self._btn_load_lyr)

        self._btn_save_draft = QPushButton("Save Project", ribbon)
        self._btn_save_draft.setToolTip("Save current project (Ctrl+S) so you can reopen right where you left off")
        self._btn_save_draft.clicked.connect(self.save_project_requested.emit)
        lyr_r2.addWidget(self._btn_save_draft)

        self._resume_btn = QPushButton("Projects…", ribbon)
        self._resume_btn.setToolTip("Open Project Organizer — resume saved drafts, clean up old drafts, or export/import .zip bundles")
        self._resume_btn.clicked.connect(self.open_project_organizer)
        lyr_r2.addWidget(self._resume_btn)

        self._duet_btn = QPushButton("🎭 Duet Mode", ribbon)
        self._duet_btn.setCheckable(True)
        self._duet_btn.setStyleSheet(
            "QPushButton:checked { background-color: #6d28d9; color: #ffffff; font-weight: bold; }"
        )
        self._duet_btn.setToolTip(
            "Enable 2-pass Duet Mode:\n"
            "  [A] Singer 1 (Red/White, default)\n"
            "  [B] Singer 2 (Blue/White)\n"
            "  [C] Together (Purple/White)\n"
            "Syncs [A] + [C] in Round 1, then [B] in Round 2."
        )
        self._duet_btn.toggled.connect(self._on_duet_btn_toggled)
        lyr_r2.addWidget(self._duet_btn)

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
        self._line_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._line_tree.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._line_tree.setStyleSheet(
            "QTreeWidget { background-color: #191c22; border: 1px solid #2e3440; font-size: 12px; }"
            "QTreeWidget::item { padding: 3px 4px; }"
            "QTreeWidget::item:selected { background-color: #2b5b84; color: #ffffff; }"
        )
        self._line_tree.itemClicked.connect(self._on_tree_clicked)
        self._line_tree.itemDoubleClicked.connect(self._on_tree_double_clicked)

        right_box = QWidget(splitter)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self._teleprompter = _RollingTeleprompterWidget(right_box)
        self._teleprompter.end_phrase_requested.connect(self._end_current_phrase)
        self._teleprompter.line_style_cycle_requested.connect(self._cycle_line_duet_style)
        self._teleprompter.page_edit_requested.connect(self._open_page_lyrics_editor)
        self._waveform = _TapWaveformStrip(self.audio, right_box)
        self._waveform.playhead_scrubbed.connect(self.playhead_scrubbed.emit)

        right_layout.addWidget(self._teleprompter, 1)
        right_layout.addWidget(self._waveform)

        splitter.addWidget(self._line_tree)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(1, 1)

        # Duet Mode Setup & Round Switcher Banner
        self._duet_banner = QFrame(self)
        self._duet_banner.setObjectName("tapSyncDuetBanner")
        db_layout = QHBoxLayout(self._duet_banner)
        db_layout.setContentsMargins(10, 6, 10, 6)
        db_layout.setSpacing(8)
        self._duet_info_lbl = QLabel("", self._duet_banner)
        self._duet_info_lbl.setWordWrap(True)
        self._duet_info_lbl.setTextFormat(Qt.TextFormat.RichText)
        db_layout.addWidget(self._duet_info_lbl, 1)

        # Setup-phase style assignment buttons + Ready button
        self._btn_duet_a = QPushButton("[A] Singer 1", self._duet_banner)
        self._btn_duet_a.setToolTip("Assign selected line(s) to Singer 1 — Style [A] (Red/White, Round 1)  [Shortcut: 1]")
        self._btn_duet_a.setStyleSheet(
            "background-color: #7f1d1d; color: #fecaca; border: 1px solid #f87171; font-weight: bold; padding: 3px 10px;"
        )
        self._btn_duet_a.clicked.connect(lambda: self._assign_duet_style_to_selection("A"))
        db_layout.addWidget(self._btn_duet_a)

        self._btn_duet_b = QPushButton("[B] Singer 2", self._duet_banner)
        self._btn_duet_b.setToolTip("Assign selected line(s) to Singer 2 — Style [B] (Blue/White, Round 2)  [Shortcut: 2]")
        self._btn_duet_b.setStyleSheet(
            "background-color: #1e3a8a; color: #bfdbfe; border: 1px solid #60a5fa; font-weight: bold; padding: 3px 10px;"
        )
        self._btn_duet_b.clicked.connect(lambda: self._assign_duet_style_to_selection("B"))
        db_layout.addWidget(self._btn_duet_b)

        self._btn_duet_c = QPushButton("[C] Together", self._duet_banner)
        self._btn_duet_c.setToolTip("Assign selected line(s) to Both Singers — Style [C] (Purple/White, Round 1)  [Shortcut: 3]")
        self._btn_duet_c.setStyleSheet(
            "background-color: #581c87; color: #e9d5ff; border: 1px solid #c084fc; font-weight: bold; padding: 3px 10px;"
        )
        self._btn_duet_c.clicked.connect(lambda: self._assign_duet_style_to_selection("C"))
        db_layout.addWidget(self._btn_duet_c)

        self._btn_duet_ready = QPushButton("✓ Ready to Sync", self._duet_banner)
        self._btn_duet_ready.setToolTip("Finish assigning line styles and cue Duet Sync")
        self._btn_duet_ready.setStyleSheet(
            "background-color: #15803d; color: #ffffff; font-weight: bold; padding: 4px 14px;"
        )
        self._btn_duet_ready.clicked.connect(self.finish_duet_setup)
        db_layout.addWidget(self._btn_duet_ready)

        # Sync-phase round switcher buttons + Edit Styles button
        self._btn_pass1 = QPushButton("Round 1: [A] && [C]", self._duet_banner)
        self._btn_pass1.setCheckable(True)
        self._btn_pass1.setToolTip("Sync Singer 1 [A] (Red/White) and Together [C] (Purple/White); [B] is greyed out")
        self._btn_pass1.clicked.connect(lambda: self._set_duet_pass(1, rewind_to_start=False))
        db_layout.addWidget(self._btn_pass1)

        self._btn_pass2 = QPushButton("Round 2: [B]", self._duet_banner)
        self._btn_pass2.setCheckable(True)
        self._btn_pass2.setToolTip("Sync Singer 2 [B] (Blue/White); [A] and [C] are greyed out")
        self._btn_pass2.clicked.connect(lambda: self._set_duet_pass(2, rewind_to_start=False))
        db_layout.addWidget(self._btn_pass2)

        self._btn_duet_edit_styles = QPushButton("Edit Line Styles…", self._duet_banner)
        self._btn_duet_edit_styles.setToolTip("Re-open Duet Setup to adjust [A] / [B] / [C] line assignments")
        self._btn_duet_edit_styles.setStyleSheet("background-color: #334155; color: #e2e8f0; padding: 3px 10px;")
        self._btn_duet_edit_styles.clicked.connect(self.enter_duet_setup)
        db_layout.addWidget(self._btn_duet_edit_styles)

        for db_btn in self._duet_banner.findChildren(QPushButton):
            db_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._duet_banner.setVisible(False)

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
        root.addWidget(self._duet_banner)
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

    @property
    def can_undo(self) -> bool:
        return (not self._locked) and (not self._is_syncing) and bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return (not self._locked) and (not self._is_syncing) and bool(self._redo_stack)

    @property
    def undo_label(self) -> str:
        return self._undo_stack[-1][0] if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo_stack[-1][0] if self.can_redo else ""

    def _clear_edit_history(self) -> None:
        if self._undo_stack or self._redo_stack:
            self._undo_stack.clear()
            self._redo_stack.clear()
            self.history_changed.emit()

    def _push_undo_snapshot(self, label: str) -> None:
        self._undo_stack.append(
            (label, deepcopy(self.draft), self._cursor, self._teleprompter.preview_flat_idx)
        )
        if len(self._undo_stack) > self._max_history:
            del self._undo_stack[: len(self._undo_stack) - self._max_history]
        self._redo_stack.clear()
        self.history_changed.emit()

    def _restore_snapshot(
        self,
        draft: TapSyncDraft,
        cursor: Optional[Tuple[int, int, int]],
        preview_flat_idx: Optional[int],
    ) -> None:
        self._apply_draft(draft, load_audio=False, clear_history=False)
        flat = self.draft.flat_lines()
        if cursor is not None and self._get_syl(cursor) is not None:
            self._cursor = cursor
        if preview_flat_idx is not None and flat:
            self._teleprompter.preview_flat_idx = max(0, min(preview_flat_idx, len(flat) - 1))
        else:
            self._teleprompter.preview_flat_idx = None
        self._set_dirty(True)
        self._autosave()
        self._sync_visuals()
        self.history_changed.emit()
        self.history_changed.emit()

    def undo_edit(self) -> bool:
        """Undo the last lyric edit in Tap Sync (Ctrl+Z)."""
        if not self.can_undo:
            return False
        label, prev_draft, prev_cursor, prev_flat_idx = self._undo_stack.pop()
        self._redo_stack.append(
            (label, deepcopy(self.draft), self._cursor, self._teleprompter.preview_flat_idx)
        )
        self._restore_snapshot(prev_draft, prev_cursor, prev_flat_idx)
        self.status_message.emit(f"Undid {label}", 3000)
        return True

    def redo_edit(self) -> bool:
        """Redo the last undone lyric edit in Tap Sync (Ctrl+Y / Ctrl+Shift+Z)."""
        if not self.can_redo:
            return False
        label, next_draft, next_cursor, next_flat_idx = self._redo_stack.pop()
        self._undo_stack.append(
            (label, deepcopy(self.draft), self._cursor, self._teleprompter.preview_flat_idx)
        )
        self._restore_snapshot(next_draft, next_cursor, next_flat_idx)
        self.status_message.emit(f"Redid {label}", 3000)
        return True

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
        self.history_changed.emit()

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
            blocked_tip if has_synced else "Load a .txt, .kbp, or .mjkp file"
        )
        # Project Organizer is always accessible (opens selected project in a new window if this one is busy)
        self._resume_btn.setEnabled(True)
        self._resume_btn.setToolTip(
            "Open Project Organizer — browse Saved Projects, Autosaves, or export/import .zip bundles"
        )

        # Editing / tapping / building controls are enabled whenever unfrozen
        can_edit = not locked
        in_setup = self.draft.duet_mode and self._duet_setup_mode
        self._duet_btn.setEnabled(can_edit)
        self._btn_paste.setEnabled(can_edit)
        self._btn_save_draft.setEnabled(can_edit)
        self._sync_btn.setEnabled(can_edit and not self._is_syncing and not in_setup)
        self._sync_btn.setToolTip(
            "Click '✓ Ready to Sync' in the Duet bar first"
            if in_setup
            else "Start or resume tap-syncing from the current cursor (with pre-roll)"
        )
        self._pause_btn.setEnabled(can_edit and self._is_syncing)
        self._btn_back_line.setEnabled(can_edit and not in_setup)
        self._btn_retap.setEnabled(can_edit and not in_setup)
        self._btn_end_phrase.setEnabled(can_edit and not in_setup)
        self._btn_undo_tap.setEnabled(can_edit and not in_setup)
        self._btn_clear_line.setEnabled(can_edit)
        self._btn_clear_all.setEnabled(can_edit)
        self._tpl_btn.setEnabled(can_edit)
        self._build_btn.setEnabled(can_edit)
        self._update_duet_ui()

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
        """Save a background recovery autosave when the user has unsaved Tap Sync edits."""
        if not self.draft.pages:
            return None
        # Only autosave when there are actual unsaved Tap Sync edits (never on untouched imports)
        if not self._dirty:
            return None
        if self._has_synced_project and self._locked:
            return None
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)
        target = default_autosaves_dir() / self.draft.default_archive_filename()
        try:
            return self.draft.save_json(target)
        except Exception:
            return None

    def reset_fresh(self) -> None:
        """Autosave any dirty outgoing draft, then start a clean empty draft."""
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

    def open_project_organizer(self) -> None:
        self.archive_current_draft()
        parent = self.window() if self.window() is not None else self
        dlg = ProjectOrganizerDialog(parent)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_project_path is not None:
            self.open_project_requested.emit(str(dlg.selected_project_path))

    def _autosave(self) -> None:
        self.archive_current_draft()

    def _apply_draft(self, draft: TapSyncDraft, load_audio: bool = True, clear_history: bool = True) -> bool:
        if self._is_syncing:
            self.stop_syncing()
        if clear_history:
            self._clear_edit_history()
        self.draft = draft
        self._teleprompter.draft = draft
        self._teleprompter.preview_flat_idx = None
        self._teleprompter.invalidate_cache()
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

        self._duet_setup_mode = False
        self._duet_btn.blockSignals(True)
        self._duet_btn.setChecked(bool(draft.duet_mode))
        self._duet_btn.blockSignals(False)

        self._cursor = draft.first_unsynced_coord()
        if self._cursor is None:
            if draft.duet_mode:
                self._cursor = draft.first_coord_for_pass(draft.duet_pass)
            elif draft.pages and draft.pages[0].lines and draft.pages[0].lines[0].syllables:
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
        self._update_lock_ui()
        self._sync_visuals()
        self.history_changed.emit()
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
        dlg = _LyricsInputDialog(self.draft, self, settings=self._settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        old_text = self.draft.to_lyrics_text()
        if (
            dlg.lyrics_text.strip() == old_text.strip()
            and dlg.title_text == self.draft.title
            and dlg.artist_text == self.draft.artist
        ):
            self.setFocus()
            return
        self._push_undo_snapshot("Edit Lyrics")
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
        new_draft.duet_mode = self.draft.duet_mode
        new_draft.duet_pass = self.draft.duet_pass
        # Preserve existing line styles where line text matches
        old_styles_by_text: Dict[str, List[str]] = {}
        for _, _, old_ln in self.draft.flat_lines():
            if old_ln.style != "A":
                key = old_ln.full_text.strip()
                old_styles_by_text.setdefault(key, []).append(old_ln.style)
        for _, _, new_ln in new_draft.flat_lines():
            bucket = old_styles_by_text.get(new_ln.full_text.strip())
            if bucket:
                new_ln.style = bucket.pop(0)
        was_setup = self._duet_setup_mode
        self._apply_draft(new_draft, load_audio=False, clear_history=False)
        if was_setup and new_draft.duet_mode:
            self.enter_duet_setup()
        self._set_dirty(True)
        self._autosave()
        self.setFocus()

    def load_lyrics_txt(self, path: str | Path) -> bool:
        if self._locked or self._has_synced_project:
            return False
        p = Path(path)
        try:
            text = read_kbp_text(p, allow_bom=True)
        except Exception as exc:
            QMessageBox.critical(self, "Load Lyrics Failed", f"Could not read lyrics file:\n{exc}")
            return False
        draft = TapSyncDraft.from_lyrics_text(text)
        if " - " in p.stem:
            artist_part, title_part = p.stem.split(" - ", 1)
            draft.artist = artist_part.strip()
            draft.title = title_part.strip()
        else:
            draft.title = p.stem
        draft.audio_path = self.draft.audio_path or (str(self.audio.main_path) if self.audio.main_path else "")
        draft.vocal_path = self.draft.vocal_path or (str(self.audio.vocal_path) if self.audio.vocal_path else "")
        draft.template_kbp_path = self.draft.template_kbp_path
        self._apply_draft(draft, load_audio=False)
        self._set_dirty(True)
        self._autosave()
        self.status_message.emit(f"Loaded lyrics from {p.name} ({draft.total_syllables()} syllables)", 4500)
        self.setFocus()
        return True

    def _load_lyrics_or_kbp_dialog(self) -> None:
        if self._locked or self._has_synced_project:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load File",
            "",
            "Supported Files (*.txt *.kbp *.mjkp);;Text Files (*.txt);;KBP Files (*.kbp);;Matt Joy Karaoke Project (*.mjkp);;All Files (*.*)",
        )
        if not path:
            return
        p = Path(path)
        suf = p.suffix.lower()
        if suf == ".mjkp":
            self.load_draft_file(p)
        elif suf == ".kbp":
            self.load_unsynced_kbp(p)
            self._autosave()
            self.setFocus()
        else:
            self.load_lyrics_txt(p)

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
            register_saved_project(self._draft_file_path)
            self._set_dirty(False)
            self.status_message.emit(f"Saved Tap Sync draft: {self._draft_file_path.name}", 4000)
            return True
        return self._save_draft_dialog()

    def save_draft_as(self) -> bool:
        """Prompt for a file path and save the current Tap Sync project draft."""
        return self._save_draft_dialog()

    def _suggest_draft_filename(self) -> str:
        return suggest_project_filename(
            self.draft.artist,
            self.draft.title,
            ext=".mjkp",
            audio_path=self.audio.main_path,
            fallback_stem="",
        )

    def _save_draft_dialog(self) -> bool:
        if not self.draft.pages:
            return False
        if self.audio.main_path:
            self.draft.audio_path = str(self.audio.main_path)
        if self.audio.vocal_path:
            self.draft.vocal_path = str(self.audio.vocal_path)
        if self._draft_file_path is not None:
            default_target = str(self._draft_file_path)
        else:
            suggested_name = self._suggest_draft_filename()
            default_dir = self.audio.main_path.parent if self.audio.main_path else None
            if default_dir is not None and suggested_name:
                default_target = str(default_dir / suggested_name)
            elif default_dir is not None:
                default_target = str(default_dir)
            else:
                default_target = suggested_name
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", default_target, "Matt Joy Karaoke Project (*.mjkp)")
        if not path:
            return False
        self._draft_file_path = self.draft.save_json(path)
        register_saved_project(self._draft_file_path)
        self._set_dirty(False)
        self.status_message.emit(f"Saved Tap Sync draft: {self._draft_file_path.name}", 4000)
        return True

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
            self._teleprompter.invalidate_cache()
            self._rebuild_tree()
            self._teleprompter.update()
            self._autosave()
            self.status_message.emit(f"Style template set from {Path(path).name}", 4000)

    @staticmethod
    def _is_autosave_file(path: Path) -> bool:
        try:
            rp = path.resolve()
            for folder in (default_autosaves_dir(), default_drafts_dir()):
                if rp.parent == folder.resolve():
                    return True
        except Exception:
            pass
        return False

    def load_draft_file(self, path: str | Path) -> bool:
        p = Path(path)
        try:
            draft = TapSyncDraft.load_json(p)
        except Exception as exc:
            QMessageBox.critical(self, "Load Draft Failed", f"Could not read .mjkp project file:\n{exc}")
            return False
        self._draft_file_path = p
        self._apply_draft(draft, load_audio=True)
        self._set_dirty(False)
        if not self._is_autosave_file(p):
            register_saved_project(p)
        self.status_message.emit(
            f"Loaded project {p.name} ({draft.synced_syllables()}/{draft.total_syllables()} syllables synced)",
            4500,
        )
        self.setFocus()
        return True

    def _on_duet_btn_toggled(self, checked: bool) -> None:
        self.set_duet_mode(checked, enter_setup=checked)

    def set_duet_mode(self, enabled: bool, enter_setup: bool = True) -> None:
        if self._is_syncing:
            self.stop_syncing()
        val = bool(enabled)
        if self.draft.duet_mode != val:
            self.draft.duet_mode = val
            self._set_dirty(True)
        self._duet_btn.blockSignals(True)
        self._duet_btn.setChecked(val)
        self._duet_btn.blockSignals(False)

        if val:
            self._duet_setup_mode = bool(enter_setup)
            self.draft.duet_pass = 1
            self._cursor = self.draft.first_unsynced_coord_for_pass(1) or self.draft.first_coord_for_pass(1)
            if self._duet_setup_mode:
                self._teleprompter.preview_flat_idx = 0 if self.draft.flat_lines() else None
        else:
            self._duet_setup_mode = False
            self._teleprompter.preview_flat_idx = None
            self._cursor = self.draft.first_unsynced_coord()
            if self._cursor is None and self.draft.pages and self.draft.pages[0].lines and self.draft.pages[0].lines[0].syllables:
                self._cursor = (0, 0, 0)

        self._open_coord = None
        self._punch_line = None
        self._rebuild_tree()
        self._update_lock_ui()
        self._sync_visuals()
        self.history_changed.emit()
        self.setFocus()

    def enter_duet_setup(self) -> None:
        if self._locked:
            return
        if self._is_syncing:
            self.stop_syncing()
        self.draft.duet_mode = True
        self._duet_setup_mode = True
        self._duet_btn.blockSignals(True)
        self._duet_btn.setChecked(True)
        self._duet_btn.blockSignals(False)
        self._rebuild_tree()
        self._update_lock_ui()
        self._sync_visuals()
        self.history_changed.emit()
        self.setFocus()

    def finish_duet_setup(self) -> None:
        """Exit Duet Setup ('✓ Ready to Sync') and cue the next incomplete round."""
        if not self.draft.duet_mode:
            return
        self._duet_setup_mode = False
        self._teleprompter.preview_flat_idx = None
        s1, t1 = self.draft.pass_syllable_counts(1)
        s2, t2 = self.draft.pass_syllable_counts(2)
        if t1 > 0 and s1 < t1:
            target_pass = 1
        elif t2 > 0 and s2 < t2:
            target_pass = 2
        else:
            target_pass = 1 if t1 > 0 else 2
        self._set_duet_pass(target_pass, rewind_to_start=False)
        round_desc = "Round 1 ([A] Singer 1 + [C] Together)" if target_pass == 1 else "Round 2 ([B] Singer 2)"
        self.status_message.emit(f"Duet Mode ready — {round_desc}. Press Space or '● Start Sync' to begin!", 5000)

    def _set_duet_pass(self, duet_pass: int, rewind_to_start: bool = False) -> None:
        if self._is_syncing:
            self.stop_syncing()
        self.draft.duet_pass = 2 if duet_pass == 2 else 1
        self._duet_setup_mode = False
        self._teleprompter.preview_flat_idx = None
        self._cursor = (
            self.draft.first_unsynced_coord_for_pass(self.draft.duet_pass)
            or self.draft.first_coord_for_pass(self.draft.duet_pass)
        )
        self._open_coord = None
        self._last_released_coord = None
        self._punch_line = None
        self._pending_punch_line = None
        self._pending_seek_cs = None
        if rewind_to_start:
            self.audio.set_position_cs(0.0, scrub_audio=False)
            self._waveform.playhead_cs = 0.0
        self._rebuild_tree()
        self._update_lock_ui()
        self._sync_visuals()
        self.history_changed.emit()
        self.setFocus()

    def _assign_duet_style_to_selection(self, style_letter: str, advance_line: bool = False) -> None:
        if self._locked or not self.draft.pages:
            return
        st = style_letter.upper()
        coords: List[Tuple[int, int]] = []
        for item in self._line_tree.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[1] >= 0:
                coords.append((data[0], data[1]))
        if not coords:
            flat = self.draft.flat_lines()
            if self._teleprompter.preview_flat_idx is not None and 0 <= self._teleprompter.preview_flat_idx < len(flat):
                p_i, l_i, _ = flat[self._teleprompter.preview_flat_idx]
                coords.append((p_i, l_i))
            else:
                sel = self._selected_line_coord()
                if sel is not None:
                    coords.append(sel)
        if not coords:
            return

        changed = False
        for p_i, l_i in coords:
            ln = self.draft.pages[p_i].lines[l_i]
            if ln.style != st:
                ln.style = st
                changed = True
            self._update_tree_line(p_i, l_i)
        if changed:
            self._set_dirty(True)
            self._autosave()

        if advance_line and len(coords) == 1:
            flat = self.draft.flat_lines()
            for idx, (p_i, l_i, _) in enumerate(flat):
                if (p_i, l_i) == coords[0] and idx + 1 < len(flat):
                    np_i, nl_i, _ = flat[idx + 1]
                    nxt_item = self._item_by_coord.get((np_i, nl_i))
                    if nxt_item is not None:
                        self._line_tree.blockSignals(True)
                        self._line_tree.clearSelection()
                        self._line_tree.setCurrentItem(nxt_item)
                        nxt_item.setSelected(True)
                        self._line_tree.scrollToItem(nxt_item)
                        self._line_tree.blockSignals(False)
                    self._teleprompter.preview_flat_idx = idx + 1
                    break

        self._update_duet_ui()
        self._teleprompter.invalidate_cache()
        self._teleprompter.update()
        self._waveform.update()
        self.setFocus()

    def _cycle_line_duet_style(self, p_idx: int, l_idx: int) -> None:
        if self._locked or not (0 <= p_idx < len(self.draft.pages)):
            return
        lines = self.draft.pages[p_idx].lines
        if not (0 <= l_idx < len(lines)):
            return
        ln = lines[l_idx]
        cur = (ln.style or "A").upper()
        nxt = "B" if cur == "A" else ("C" if cur == "B" else "A")
        ln.style = nxt
        self._set_dirty(True)
        self._update_tree_line(p_idx, l_idx)
        item = self._item_by_coord.get((p_idx, l_idx))
        if item is not None:
            self._line_tree.blockSignals(True)
            self._line_tree.clearSelection()
            self._line_tree.setCurrentItem(item)
            item.setSelected(True)
            self._line_tree.scrollToItem(item)
            self._line_tree.blockSignals(False)
        self._autosave()
        self._update_duet_ui()
        self._teleprompter.invalidate_cache()
        self._teleprompter.update()
        self._waveform.update()
        self.setFocus()

    def _update_duet_ui(self) -> None:
        if not hasattr(self, "_duet_banner"):
            return
        if not self.draft.duet_mode:
            self._duet_banner.setVisible(False)
            return

        setup = self._duet_setup_mode
        can_edit = not self._locked
        self._btn_duet_a.setVisible(setup)
        self._btn_duet_b.setVisible(setup)
        self._btn_duet_c.setVisible(setup)
        self._btn_duet_ready.setVisible(setup)
        self._btn_pass1.setVisible(not setup)
        self._btn_pass2.setVisible(not setup)
        self._btn_duet_edit_styles.setVisible(not setup)

        for w in (self._btn_duet_a, self._btn_duet_b, self._btn_duet_c, self._btn_duet_ready, self._btn_pass1, self._btn_pass2, self._btn_duet_edit_styles):
            w.setEnabled(can_edit)

        s1, t1 = self.draft.pass_syllable_counts(1)
        s2, t2 = self.draft.pass_syllable_counts(2)

        if setup:
            self._duet_banner.setStyleSheet(
                "QFrame#tapSyncDuetBanner { background-color: #241934; border: 1px solid #7e22ce; border-radius: 4px; }"
                "QLabel { color: #f3e8ff; font-size: 11px; }"
            )
            self._duet_info_lbl.setText(
                "<b>🎭 Duet Setup — Assign Singer Styles:</b>  "
                "<span style='color:#fca5a5;'><b>[A] Singer 1</b> (Red/White, default)</span> · "
                "<span style='color:#93c5fd;'><b>[B] Singer 2</b> (Blue/White)</span> · "
                "<span style='color:#d8b4fe;'><b>[C] Sung Together</b> (Purple/White)</span><br>"
                "Click lines on the teleprompter to cycle <b>[A]→[B]→[C]</b>, or select lines on the left (keys <b>1</b>, <b>2</b>, <b>3</b>), "
                "then click <b>✓ Ready to Sync</b>."
            )
        else:
            p = self.draft.duet_pass
            self._duet_banner.setStyleSheet(
                "QFrame#tapSyncDuetBanner { background-color: #1b2232; border: 1px solid #475569; border-radius: 4px; }"
                "QLabel { color: #e2e8f0; font-size: 11px; }"
            )
            if p == 1:
                self._duet_info_lbl.setText(
                    "<b>🎭 Duet Mode — Round 1 Active:</b>  Syncing "
                    "<span style='color:#fca5a5;'><b>[A] Singer 1</b> (Red/White)</span> &amp; "
                    "<span style='color:#d8b4fe;'><b>[C] Together</b> (Purple/White)</span> · "
                    "<span style='color:#94a3b8;'>[B] Singer 2 is greyed out for Round 2.</span>"
                )
            else:
                self._duet_info_lbl.setText(
                    "<b>🎭 Duet Mode — Round 2 Active:</b>  Syncing "
                    "<span style='color:#93c5fd;'><b>[B] Singer 2</b> (Blue/White)</span> · "
                    "<span style='color:#94a3b8;'>[A] Singer 1 &amp; [C] Together are greyed out.</span>"
                )
            self._btn_pass1.blockSignals(True)
            self._btn_pass1.setChecked(p == 1)
            self._btn_pass1.setText(f"Round 1: [A] && [C] ({s1}/{t1})")
            self._btn_pass1.setStyleSheet(
                "background-color: #6d28d9; color: #ffffff; font-weight: bold; padding: 3px 10px;"
                if p == 1
                else "background-color: #27303f; color: #cbd5e1; padding: 3px 10px;"
            )
            self._btn_pass1.blockSignals(False)

            self._btn_pass2.blockSignals(True)
            self._btn_pass2.setChecked(p == 2)
            self._btn_pass2.setText(f"Round 2: [B] ({s2}/{t2})")
            self._btn_pass2.setStyleSheet(
                "background-color: #1d4ed8; color: #ffffff; font-weight: bold; padding: 3px 10px;"
                if p == 2
                else "background-color: #27303f; color: #cbd5e1; padding: 3px 10px;"
            )
            self._btn_pass2.blockSignals(False)

        self._duet_banner.setVisible(True)

    def _rebuild_tree(self) -> None:
        self._line_tree.blockSignals(True)
        self._line_tree.clear()
        self._item_by_coord.clear()
        hdr_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        header = self.draft.resolve_header()
        for p_idx, page in enumerate(self.draft.pages):
            p_item = QTreeWidgetItem([f"Page {p_idx + 1}"])
            p_item.setFont(0, hdr_font)
            p_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            p_item.setBackground(0, QBrush(QColor(34, 40, 52)))
            p_item.setForeground(0, QBrush(QColor(155, 185, 225)))
            p_item.setData(0, Qt.ItemDataRole.UserRole, (p_idx, -1))
            p_item.setToolTip(0, f"Double-click to edit Page {p_idx + 1} lyrics")
            self._line_tree.addTopLevelItem(p_item)
            for l_idx, line in enumerate(page.lines):
                child = QTreeWidgetItem([self._format_tree_line(line)])
                child.setData(0, Qt.ItemDataRole.UserRole, (p_idx, l_idx))
                self._style_tree_item(child, line, p_idx=p_idx, header=header)
                p_item.addChild(child)
                self._item_by_coord[(p_idx, l_idx)] = child
            p_item.setExpanded(True)
        self._line_tree.blockSignals(False)
        self._highlight_cursor_in_tree()

    def _format_tree_line(self, line: TapLine) -> str:
        st = line.sync_state
        dot = "●" if st == "full" else ("◐" if st == "partial" else "○")
        txt = line.full_text.strip() or "(empty)"
        if self.draft.duet_mode:
            return f"{dot} [{(line.style or 'A').upper()}] {txt}"
        return f"{dot} {txt}"

    def _style_tree_item(
        self,
        item: QTreeWidgetItem,
        line: TapLine,
        p_idx: Optional[int] = None,
        header: Optional[KbpHeader] = None,
    ) -> None:
        hdr = header if header is not None else self.draft.resolve_header()
        line_w, usable_w, style_used = _measure_line_16_9(line.full_text, hdr, line.style)
        ratio = line_w / usable_w if usable_w > 0 else 0.0
        pct = int(round(ratio * 100))
        page_hint = f" — Double-click to edit Page {p_idx + 1}" if p_idx is not None else ""
        if ratio > _BORDER_OVERFLOW_RATIO:
            item.setToolTip(
                0,
                f"16:9 width: {pct}% ({style_used.font_name} {style_used.font_size}pt) — Exceeds 16:9 border!{page_hint}",
            )
        elif ratio >= _BORDER_NEAR_RATIO:
            item.setToolTip(
                0,
                f"16:9 width: {pct}% ({style_used.font_name} {style_used.font_size}pt) — Close to 16:9 border{page_hint}",
            )
        else:
            item.setToolTip(
                0,
                f"16:9 width: {pct}% ({style_used.font_name} {style_used.font_size}pt){page_hint}",
            )

        st = line.sync_state
        if self.draft.duet_mode:
            letter = (line.style or "A").upper()
            in_pass = True if self._duet_setup_mode else self.draft.is_line_in_pass(line, self.draft.duet_pass)
            if not in_pass:
                item.setForeground(0, QBrush(QColor(100, 110, 126)))
                return
            if not self._duet_setup_mode and st == "full":
                item.setForeground(0, QBrush(QColor(110, 231, 183)))
                return
            if not self._duet_setup_mode and st == "partial":
                item.setForeground(0, QBrush(QColor(251, 191, 36)))
                return
            if letter == "B":
                item.setForeground(0, QBrush(QColor(147, 197, 253)))
            elif letter == "C":
                item.setForeground(0, QBrush(QColor(216, 180, 254)))
            else:
                item.setForeground(0, QBrush(QColor(252, 165, 165)))
            return

        if st == "full":
            item.setForeground(0, QBrush(QColor(110, 231, 183)))
        elif st == "partial":
            item.setForeground(0, QBrush(QColor(251, 191, 36)))
        elif ratio > _BORDER_OVERFLOW_RATIO:
            item.setForeground(0, QBrush(QColor(252, 165, 165)))
        elif ratio >= _BORDER_NEAR_RATIO:
            item.setForeground(0, QBrush(QColor(253, 230, 138)))
        else:
            item.setForeground(0, QBrush(QColor(203, 213, 225)))

    def _update_tree_line(self, p_idx: int, l_idx: int) -> None:
        item = self._item_by_coord.get((p_idx, l_idx))
        if item is not None and 0 <= p_idx < len(self.draft.pages) and 0 <= l_idx < len(self.draft.pages[p_idx].lines):
            line = self.draft.pages[p_idx].lines[l_idx]
            item.setText(0, self._format_tree_line(line))
            self._style_tree_item(item, line, p_idx=p_idx)

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

        if self.draft.duet_mode and self._duet_setup_mode:
            flat = self.draft.flat_lines()
            for idx, (fp, fl, _) in enumerate(flat):
                if (fp, fl) == (p_idx, l_idx):
                    self._teleprompter.preview_flat_idx = idx
                    break
            self._teleprompter.update()
            self.setFocus()
            return

        if self.draft.duet_mode and not self.draft.is_line_in_pass(line):
            # Preview greyed-out line from the other singer without moving the active-pass cursor onto it
            flat = self.draft.flat_lines()
            for idx, (fp, fl, _) in enumerate(flat):
                if (fp, fl) == (p_idx, l_idx):
                    self._teleprompter.preview_flat_idx = idx
                    break
            if line.first_start_cs is not None:
                self.playhead_scrubbed.emit(float(max(0, line.first_start_cs - self.draft.pre_roll_cs)))
            self._teleprompter.update()
            self.setFocus()
            return

        # Place cursor on first unsynced syllable in this line (or syllable 0 if already synced)
        s_idx = next((i for i, s in enumerate(line.syllables) if not s.has_start), 0)
        self._cursor = (p_idx, l_idx, s_idx)
        self._open_coord = None
        self._punch_line = None
        self._teleprompter.preview_flat_idx = None
        if line.first_start_cs is not None:
            self.playhead_scrubbed.emit(float(max(0, line.first_start_cs - self.draft.pre_roll_cs)))
        self._sync_visuals()
        self.setFocus()

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        p_idx, l_idx = data
        self._open_page_lyrics_editor(p_idx, max(0, l_idx))

    def _open_page_lyrics_editor(self, page_idx: int, line_idx: int = 0) -> None:
        if self._locked or self._is_syncing:
            return
        if not (0 <= page_idx < len(self.draft.pages)):
            return
        page = self.draft.pages[page_idx]
        dlg = _PageLyricsEditDialog(
            page_idx=page_idx,
            page=page,
            header=self.draft.resolve_header(),
            focus_line_idx=line_idx,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.setFocus()
            return

        old_page_text = "\n".join(ln.slash_text for ln in page.lines if ln.syllables)
        if dlg.lyrics_text.strip() == old_page_text.strip():
            self.setFocus()
            return

        self._push_undo_snapshot(f"Edit Page {page_idx + 1} Lyrics")
        focus_page_idx, inserted = self.draft.replace_page_from_text(page_idx, dlg.lyrics_text)
        self._open_coord = None
        self._last_released_coord = None
        self._punch_line = None
        self._pending_punch_line = None

        # Keep the user right on the page/line they just edited
        if self.draft.pages:
            fp = max(0, min(focus_page_idx, len(self.draft.pages) - 1))
            fl = max(0, min(line_idx, len(self.draft.pages[fp].lines) - 1))
            target_line = self.draft.pages[fp].lines[fl]
            s_idx = next((i for i, s in enumerate(target_line.syllables) if not s.has_start), 0)
            if not self.draft.duet_mode or self.draft.is_line_in_pass(target_line):
                self._cursor = (fp, fl, s_idx)
            else:
                self._cursor = (
                    self.draft.first_unsynced_coord_for_pass(self.draft.duet_pass)
                    or self.draft.first_coord_for_pass(self.draft.duet_pass)
                )
            flat = self.draft.flat_lines()
            flat_idx = next((idx for idx, (p_i, l_i, _) in enumerate(flat) if (p_i, l_i) == (fp, fl)), None)
            self._teleprompter.preview_flat_idx = flat_idx
        else:
            self._cursor = None
            self._teleprompter.preview_flat_idx = None

        self._rebuild_tree()
        self._set_dirty(True)
        self._autosave()
        self._sync_visuals()
        self.history_changed.emit()
        if inserted > 1:
            self.status_message.emit(f"Updated Page {page_idx + 1} (split into {inserted} pages)", 3500)
        elif inserted == 0:
            self.status_message.emit(f"Removed empty Page {page_idx + 1}", 3500)
        else:
            self.status_message.emit(f"Updated Page {page_idx + 1} lyrics", 3000)
        self.setFocus()

    def _sync_visuals(self) -> None:
        self._teleprompter.cursor = self._cursor
        self._teleprompter.open_coord = self._open_coord
        self._teleprompter.is_syncing = self._is_syncing
        self._teleprompter.is_locked = self._locked
        self._teleprompter.duet_mode = self.draft.duet_mode
        self._teleprompter.duet_pass = self.draft.duet_pass
        self._teleprompter.duet_setup_mode = self._duet_setup_mode
        self._teleprompter.punch_line = self._punch_line
        self._teleprompter.invalidate_cache()
        self._teleprompter.update()
        self._waveform.update()
        self._update_duet_ui()
        if not self._duet_setup_mode:
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
        p_i, l_i, s_i = coord
        pages = self.draft.pages
        if not (0 <= p_i < len(pages)):
            return None
        lines = pages[p_i].lines
        if not (0 <= l_i < len(lines)):
            return None
        if self.draft.is_line_in_pass(lines[l_i]) and s_i + 1 < len(lines[l_i].syllables):
            return (p_i, l_i, s_i + 1)
        for nl_i in range(l_i + 1, len(lines)):
            if lines[nl_i].syllables and self.draft.is_line_in_pass(lines[nl_i]):
                return (p_i, nl_i, 0)
        for np_i in range(p_i + 1, len(pages)):
            for nl_i, nline in enumerate(pages[np_i].lines):
                if nline.syllables and self.draft.is_line_in_pass(nline):
                    return (np_i, nl_i, 0)
        return None

    def _prev_coord(self, coord: Tuple[int, int, int]) -> Optional[Tuple[int, int, int]]:
        p_i, l_i, s_i = coord
        pages = self.draft.pages
        if not (0 <= p_i < len(pages)):
            return None
        lines = pages[p_i].lines
        if not (0 <= l_i < len(lines)):
            return None
        if self.draft.is_line_in_pass(lines[l_i]) and s_i > 0:
            return (p_i, l_i, s_i - 1)
        for pl_i in range(l_i - 1, -1, -1):
            if lines[pl_i].syllables and self.draft.is_line_in_pass(lines[pl_i]):
                return (p_i, pl_i, len(lines[pl_i].syllables) - 1)
        for pp_i in range(p_i - 1, -1, -1):
            plines = pages[pp_i].lines
            for pl_i in range(len(plines) - 1, -1, -1):
                if plines[pl_i].syllables and self.draft.is_line_in_pass(plines[pl_i]):
                    return (pp_i, pl_i, len(plines[pl_i].syllables) - 1)
        return None

    def _effective_tap_cs(self) -> int:
        raw = int(round(self.audio.smooth_position_cs))
        offset = self.draft.latency_cs if self.draft.latency_enabled else 0
        return max(0, raw - offset)

    def _pre_roll_start_for(self, coord: Tuple[int, int, int]) -> Optional[float]:
        """Compute a comfortable pre-roll playback start position before `coord` (within active pass)."""
        prev_time: Optional[int] = None
        for p_i, page in enumerate(self.draft.pages):
            for l_i, line in enumerate(page.lines):
                in_pass = self.draft.is_line_in_pass(line)
                for s_i, syl in enumerate(line.syllables):
                    if (p_i, l_i, s_i) == coord:
                        if prev_time is not None:
                            return float(max(0, prev_time - self.draft.pre_roll_cs))
                        return None
                    if not in_pass:
                        continue
                    if syl.end_cs is not None:
                        prev_time = syl.end_cs
                    elif syl.start_cs is not None:
                        prev_time = syl.start_cs
        if prev_time is not None:
            return float(max(0, prev_time - self.draft.pre_roll_cs))
        return None

    def start_syncing(self, punch_line: Optional[Tuple[int, int]] = None, seek_cs: Optional[float] = None) -> None:
        if self._locked or not self.draft.pages or (self.draft.duet_mode and self._duet_setup_mode):
            return
        if self._cursor is None:
            if self.draft.duet_mode:
                self._cursor = (
                    self.draft.first_unsynced_coord_for_pass(self.draft.duet_pass)
                    or self.draft.first_coord_for_pass(self.draft.duet_pass)
                )
            else:
                self._cursor = self.draft.first_unsynced_coord() or (0, 0, 0)
        if self._cursor is None:
            QMessageBox.information(
                self,
                "No Lines in Round",
                "There are no lines assigned to this Duet round. Click 'Edit Line Styles…' or switch rounds.",
            )
            return
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
        self._teleprompter.preview_flat_idx = None
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
        self.history_changed.emit()
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
        self.history_changed.emit()

    def toggle_syncing(self) -> None:
        if self._is_syncing:
            self.stop_syncing()
        else:
            self.start_syncing()

    def start_retap_selected_line(self) -> None:
        if self._locked or (self.draft.duet_mode and self._duet_setup_mode):
            return
        coord = self._selected_line_coord()
        if coord is None:
            return
        p_i, l_i = coord
        line = self.draft.pages[p_i].lines[l_i]
        if self.draft.duet_mode and not self.draft.is_line_in_pass(line):
            # Automatically switch to this line's duet pass when explicitly re-tapping it
            self.draft.duet_pass = 2 if (line.style or "A").upper() == "B" else 1
            self._rebuild_tree()
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
        if self._locked or (self.draft.duet_mode and self._duet_setup_mode):
            return
        flat_lines = [entry for entry in self.draft.flat_lines() if self.draft.is_line_in_pass(entry[2])]
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
        self.history_changed.emit()

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
        self.history_changed.emit()
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
        self._teleprompter.preview_flat_idx = None

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
                self._on_pass_or_song_finished()
            return
        self._sync_visuals()

    def _on_pass_or_song_finished(self) -> None:
        if self.draft.duet_mode:
            other_pass = 2 if self.draft.duet_pass == 1 else 1
            other_synced, other_total = self.draft.pass_syllable_counts(other_pass)
            if other_total > 0 and other_synced < other_total:
                cur_lbl = "Round 1 ([A] Singer 1 & [C] Together)" if self.draft.duet_pass == 1 else "Round 2 ([B] Singer 2)"
                nxt_lbl = "Round 2 ([B] Singer 2)" if other_pass == 2 else "Round 1 ([A] Singer 1 & [C] Together)"
                reply = QMessageBox.question(
                    self,
                    "Duet Round Complete",
                    f"{cur_lbl} is complete!\n\n"
                    f"Rewind to the start and switch to {nxt_lbl} ({other_total - other_synced} syllables remaining)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._set_duet_pass(other_pass, rewind_to_start=True)
                return
        self._prompt_build_after_finish()

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
        if self._locked or (self.draft.duet_mode and self._duet_setup_mode):
            return
        target = self._open_coord
        if target is None and self._cursor is not None:
            target = self._prev_coord(self._cursor)
        elif target is None:
            flat = [
                (p_i, l_i, s_i, syl)
                for p_i, page in enumerate(self.draft.pages)
                for l_i, ln in enumerate(page.lines)
                if self.draft.is_line_in_pass(ln)
                for s_i, syl in enumerate(ln.syllables)
            ]
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
        if self.draft.duet_mode and self._duet_setup_mode:
            if key in (Qt.Key.Key_1, Qt.Key.Key_A):
                self._assign_duet_style_to_selection("A", advance_line=True)
                event.accept()
                return
            if key in (Qt.Key.Key_2, Qt.Key.Key_B):
                self._assign_duet_style_to_selection("B", advance_line=True)
                event.accept()
                return
            if key in (Qt.Key.Key_3, Qt.Key.Key_C):
                self._assign_duet_style_to_selection("C", advance_line=True)
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_duet_setup()
                event.accept()
                return
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                flat = self.draft.flat_lines()
                if flat:
                    step = -1 if key == Qt.Key.Key_Up else 1
                    cur_i = self._teleprompter.preview_flat_idx if self._teleprompter.preview_flat_idx is not None else 0
                    nxt_i = max(0, min(len(flat) - 1, cur_i + step))
                    self._teleprompter.preview_flat_idx = nxt_i
                    np_i, nl_i, _ = flat[nxt_i]
                    item = self._item_by_coord.get((np_i, nl_i))
                    if item is not None:
                        self._line_tree.blockSignals(True)
                        self._line_tree.clearSelection()
                        self._line_tree.setCurrentItem(item)
                        item.setSelected(True)
                        self._line_tree.scrollToItem(item)
                        self._line_tree.blockSignals(False)
                    self._teleprompter.update()
                event.accept()
                return
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
                        self._on_pass_or_song_finished()
                    event.accept()
                    return
                self._sync_visuals()
            event.accept()
            return
        super().keyReleaseEvent(event)
