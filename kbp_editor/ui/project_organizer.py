from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import KbpProject
from ..tap_sync import (
    TapSyncDraft,
    default_autosaves_dir,
    default_saved_projects_registry,
    list_autosave_drafts,
    list_saved_projects,
    register_saved_project,
    unregister_saved_projects,
)


def _fmt_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


class ProjectOrganizerDialog(QDialog):
    """
    Unified Project Organizer with two clear tabs:
    - Saved Projects: explicitly saved .kbp files, saved .kbsync.json drafts, and imported .zip bundles
    - Autosaves: background Tap Sync recovery autosaves
    Supports Ctrl+Click / Shift+Click multi-select, portable .zip bundle export/import,
    and optional confirmation bypass ("Do not remind me again").
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        drafts_dir: Optional[Path] = None,
        registry_path: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Project Organizer — Saved Projects & Autosaves")
        self.resize(840, 480)
        self._autosaves_dir = drafts_dir or default_autosaves_dir()
        self._explicit_autosaves_dir = drafts_dir is not None
        self._registry_path = registry_path or default_saved_projects_registry()

        # Saved tab entries: (path, display_name, kind_label, progress_txt, audio_txt, saved_txt)
        self._saved_entries: List[Tuple[Path, str, str, str, str, str]] = []
        # Autosave tab entries: (path, TapSyncDraft)
        self._autosave_entries: List[Tuple[Path, TapSyncDraft]] = []
        self.selected_project_path: Optional[Path] = None
        # Backward-compatible alias
        self.selected_draft_path: Optional[Path] = None

        root = QVBoxLayout(self)
        root.setSpacing(8)

        info = QLabel(
            "<b>Saved Projects</b> lists projects you explicitly saved (.kbp, Tap Sync drafts, and imported .zip bundles). "
            "<b>Autosaves</b> holds background Tap Sync recovery snapshots. "
            "Use <b>Ctrl+Click</b> or <b>Shift+Click</b> to select multiple items."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        root.addWidget(info)

        self._tabs = QTabWidget(self)
        self._saved_table = self._make_table(["Project / Song", "Type", "Sync Progress", "Audio Files", "Last Saved"])
        self._autosave_table = self._make_table(["Autosave / Song", "Sync Progress", "Audio Files", "Last Autosaved"])
        self._tabs.addTab(self._saved_table, "Saved Projects")
        self._tabs.addTab(self._autosave_table, "Autosaves")
        self._tabs.currentChanged.connect(self._update_tab_buttons)
        root.addWidget(self._tabs, 1)

        # Keep ._table pointing to autosave table when callers inspect autosaves, or active table via helper
        self._table = self._autosave_table

        self._settings = QSettings("MattJoy", "AdvancedKBPEditor")
        row_opts = QHBoxLayout()
        self._inc_audio_cb = QCheckBox("Include main audio && vocal stem when exporting .zip bundle", self)
        self._inc_audio_cb.setChecked(self._settings.value("bundle_include_audio", True, type=bool))
        self._inc_audio_cb.toggled.connect(self._on_inc_audio_toggled)
        row_opts.addWidget(self._inc_audio_cb)
        row_opts.addStretch()
        self._confirm_del_cb = QCheckBox("Confirm before deleting / removing", self)
        self._confirm_del_cb.setChecked(self._settings.value("confirm_delete_draft", True, type=bool))
        self._confirm_del_cb.toggled.connect(self._on_confirm_del_toggled)
        row_opts.addWidget(self._confirm_del_cb)
        root.addLayout(row_opts)

        btns = QHBoxLayout()
        self._btn_open = QPushButton("Open Project", self)
        self._btn_open.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 4px 12px;")
        self._btn_open.clicked.connect(self._open_selected)
        btns.addWidget(self._btn_open)

        btn_export = QPushButton("Export Bundle (.zip)…", self)
        btn_export.setToolTip("Package the selected project (and optional audio) into a portable .zip for sharing")
        btn_export.clicked.connect(self._export_selected)
        btns.addWidget(btn_export)

        btn_import = QPushButton("Import Bundle (.zip)…", self)
        btn_import.setToolTip("Import a shared .zip project bundle and open it")
        btn_import.clicked.connect(self._import_bundle)
        btns.addWidget(btn_import)

        btns.addSpacing(12)
        self._btn_del = QPushButton("Remove Selected", self)
        self._btn_del.clicked.connect(self._delete_selected)
        btns.addWidget(self._btn_del)

        self._btn_clean = QPushButton("Clean Up Completed", self)
        self._btn_clean.setToolTip("Delete all autosaves that are already 100% synced")
        self._btn_clean.clicked.connect(self._cleanup_completed)
        btns.addWidget(self._btn_clean)

        self._btn_folder = QPushButton("Open Autosaves Folder", self)
        self._btn_folder.setToolTip(f"Open autosaves folder:\n{self._autosaves_dir}")
        self._btn_folder.clicked.connect(self._open_folder)
        btns.addWidget(self._btn_folder)

        btns.addStretch()
        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.reject)
        btns.addWidget(btn_close)
        root.addLayout(btns)

        self._refresh_tables()
        self._update_tab_buttons()

    def _make_table(self, headers: List[str]) -> QTableWidget:
        tbl = QTableWidget(0, len(headers), self)
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(headers)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        tbl.cellDoubleClicked.connect(lambda _r, _c: self._open_selected())
        return tbl

    def _is_saved_tab(self) -> bool:
        return self._tabs.currentWidget() is self._saved_table

    def _update_tab_buttons(self) -> None:
        is_saved = self._is_saved_tab()
        self._btn_open.setText("Open Project" if is_saved else "Open Autosave")
        self._btn_del.setText("Remove Selected" if is_saved else "Delete Selected")
        self._btn_del.setToolTip(
            "Remove selected project(s) from the Saved Projects list (files on disk are NOT deleted)"
            if is_saved
            else "Permanently delete selected autosave snapshot(s) (original audio files are NOT deleted)"
        )
        self._btn_clean.setVisible(not is_saved)
        self._btn_folder.setVisible(not is_saved)

    def _on_inc_audio_toggled(self, checked: bool) -> None:
        self._settings.setValue("bundle_include_audio", bool(checked))
        self._settings.sync()

    def _on_confirm_del_toggled(self, checked: bool) -> None:
        self._settings.setValue("confirm_delete_draft", bool(checked))
        self._settings.sync()

    def _inspect_saved_path(self, path: Path) -> Tuple[Path, str, str, str, str, str]:
        suffix = path.suffix.lower()
        saved_txt = _fmt_mtime(path)
        if suffix == ".json":
            try:
                draft = TapSyncDraft.load_json(path)
                synced = draft.synced_syllables()
                total = draft.total_syllables()
                pct = int(round((synced / total) * 100)) if total else 0
                prog_txt = f"{synced}/{total} ({pct}%)" if total else "Empty"
                has_main = bool(draft.audio_path and Path(draft.audio_path).is_file())
                has_voc = bool(draft.vocal_path and Path(draft.vocal_path).is_file())
                aud_txt = f"Main {'✓' if has_main else '—'}  |  Vocal {'✓' if has_voc else '—'}"
                if draft.updated_at:
                    saved_txt = draft.updated_at.replace("T", " ")
                return (path, draft.display_name, "Tap Sync Draft", prog_txt, aud_txt, saved_txt)
            except Exception:
                return (path, path.name, "Tap Sync Draft", "—", "—", saved_txt)

        # .kbp file
        try:
            proj = KbpProject.load(path)
            name = (
                f"{proj.header.artist} - {proj.header.title}"
                if (proj.header.artist and proj.header.title)
                else (proj.header.title or path.stem)
            )
            total_syls = sum(len(ln.syllables) for p in proj.pages for ln in p.lines)
            prog_txt = f"{len(proj.pages)} pages ({total_syls} syls)"
            has_main = bool(proj.header.audio_path and Path(TapSyncDraft._resolve_audio_path(proj.header.audio_path, path.parent)).is_file())
            aud_txt = f"Main {'✓' if has_main else '—'}"
            return (path, name, "Synced .kbp", prog_txt, aud_txt, saved_txt)
        except Exception:
            return (path, path.stem, ".kbp", "Unsynced", "—", saved_txt)

    def _refresh_tables(self) -> None:
        # 1) Saved Projects tab
        paths = list_saved_projects(self._registry_path)
        self._saved_entries = [self._inspect_saved_path(p) for p in paths]
        self._saved_table.setRowCount(len(self._saved_entries))
        for row, (path, name, kind, prog_txt, aud_txt, saved_txt) in enumerate(self._saved_entries):
            it0 = QTableWidgetItem(name)
            it0.setToolTip(str(path))
            self._saved_table.setItem(row, 0, it0)
            self._saved_table.setItem(row, 1, QTableWidgetItem(kind))
            self._saved_table.setItem(row, 2, QTableWidgetItem(prog_txt))
            self._saved_table.setItem(row, 3, QTableWidgetItem(aud_txt))
            self._saved_table.setItem(row, 4, QTableWidgetItem(saved_txt))
        if self._saved_entries:
            self._saved_table.selectRow(0)
        self._tabs.setTabText(0, f"Saved Projects ({len(self._saved_entries)})")

        # 2) Autosaves tab
        self._autosave_entries = list_autosave_drafts(
            autosaves_dir=self._autosaves_dir,
            include_legacy=not self._explicit_autosaves_dir,
        )
        self._autosave_table.setRowCount(len(self._autosave_entries))
        for row, (path, draft) in enumerate(self._autosave_entries):
            synced = draft.synced_syllables()
            total = draft.total_syllables()
            pct = int(round((synced / total) * 100)) if total else 0
            prog_txt = f"{synced}/{total} ({pct}%)" if total else "Empty"
            has_main = bool(draft.audio_path and Path(draft.audio_path).is_file())
            has_voc = bool(draft.vocal_path and Path(draft.vocal_path).is_file())
            aud_txt = f"Main {'✓' if has_main else '—'}  |  Vocal {'✓' if has_voc else '—'}"
            saved_txt = (draft.updated_at or "").replace("T", " ")

            it0 = QTableWidgetItem(draft.display_name)
            it0.setToolTip(str(path))
            self._autosave_table.setItem(row, 0, it0)
            self._autosave_table.setItem(row, 1, QTableWidgetItem(prog_txt))
            self._autosave_table.setItem(row, 2, QTableWidgetItem(aud_txt))
            self._autosave_table.setItem(row, 3, QTableWidgetItem(saved_txt))
        if self._autosave_entries:
            self._autosave_table.selectRow(0)
        self._tabs.setTabText(1, f"Autosaves ({len(self._autosave_entries)})")

    def _selected_rows_for(self, tbl: QTableWidget, count: int) -> List[int]:
        sel_model = tbl.selectionModel()
        rows = sorted({idx.row() for idx in sel_model.selectedRows()}) if sel_model is not None else []
        if not rows:
            cur = tbl.currentRow()
            if 0 <= cur < count:
                rows = [cur]
        return [r for r in rows if 0 <= r < count]

    def _selected_entries(self) -> List[Tuple[Path, str]]:
        if self._is_saved_tab():
            rows = self._selected_rows_for(self._saved_table, len(self._saved_entries))
            return [(self._saved_entries[r][0], self._saved_entries[r][1]) for r in rows]
        rows = self._selected_rows_for(self._autosave_table, len(self._autosave_entries))
        return [(self._autosave_entries[r][0], self._autosave_entries[r][1].display_name) for r in rows]

    def _current_path(self) -> Optional[Path]:
        if self._is_saved_tab():
            row = self._saved_table.currentRow()
            if 0 <= row < len(self._saved_entries):
                return self._saved_entries[row][0]
            sel = self._selected_entries()
            return sel[0][0] if sel else None
        row = self._autosave_table.currentRow()
        if 0 <= row < len(self._autosave_entries):
            return self._autosave_entries[row][0]
        sel = self._selected_entries()
        return sel[0][0] if sel else None

    def _open_selected(self) -> None:
        path = self._current_path()
        if path is None:
            return
        self.selected_project_path = path
        self.selected_draft_path = path
        self.accept()

    def _export_selected(self) -> None:
        path = self._current_path()
        if path is None:
            return
        try:
            if path.suffix.lower() == ".json":
                draft = TapSyncDraft.load_json(path)
            else:
                draft = TapSyncDraft.from_kbp_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not load project for export:\n{exc}")
            return
        default_name = f"{draft.display_name}.zip"
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Project Bundle", default_name, "Project Bundle (*.zip)")
        if not out_path:
            return
        try:
            draft.export_bundle_zip(out_path, include_audio=self._inc_audio_cb.isChecked())
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not export project bundle:\n{exc}")
            return
        QMessageBox.information(self, "Bundle Exported", f"Exported portable project bundle to:\n{out_path}")

    def _import_bundle(self) -> None:
        zip_path, _ = QFileDialog.getOpenFileName(self, "Import Project Bundle", "", "Project Bundle (*.zip)")
        if not zip_path:
            return
        dest_dir = QFileDialog.getExistingDirectory(self, "Choose Folder to Extract Imported Project", str(Path(zip_path).parent))
        if not dest_dir:
            return
        try:
            folder_name = Path(zip_path).stem
            target_dir = Path(dest_dir) / folder_name
            _draft, json_path = TapSyncDraft.import_bundle_zip(zip_path, target_dir)
            register_saved_project(json_path, self._registry_path)
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Could not import project bundle:\n{exc}")
            return
        self.selected_project_path = json_path
        self.selected_draft_path = json_path
        self.accept()

    def _delete_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        is_saved = self._is_saved_tab()
        if self._settings.value("confirm_delete_draft", True, type=bool):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Question)
            if is_saved:
                if len(entries) == 1:
                    msg.setWindowTitle("Remove Saved Project")
                    msg.setText(
                        f"Remove '{entries[0][1]}' from Saved Projects?\n"
                        "(Your .kbp, draft, and audio files on disk are NOT deleted.)"
                    )
                else:
                    msg.setWindowTitle("Remove Saved Projects")
                    msg.setText(
                        f"Remove {len(entries)} selected projects from Saved Projects?\n"
                        "(Your .kbp, draft, and audio files on disk are NOT deleted.)"
                    )
            else:
                if len(entries) == 1:
                    msg.setWindowTitle("Delete Autosave")
                    msg.setText(
                        f"Delete autosave '{entries[0][1]}'?\n"
                        "(Original audio files are NOT deleted.)"
                    )
                else:
                    msg.setWindowTitle("Delete Autosaves")
                    msg.setText(
                        f"Delete {len(entries)} selected autosaves?\n"
                        "(Original audio files are NOT deleted.)"
                    )
            cb = QCheckBox("Do not remind me again", msg)
            msg.setCheckBox(cb)
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            if msg.exec() != QMessageBox.StandardButton.Yes:
                return
            if cb.isChecked():
                self._settings.setValue("confirm_delete_draft", False)
                self._settings.sync()
                self._confirm_del_cb.blockSignals(True)
                self._confirm_del_cb.setChecked(False)
                self._confirm_del_cb.blockSignals(False)

        if is_saved:
            unregister_saved_projects([p for p, _ in entries], self._registry_path)
        else:
            for path, _ in entries:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
        self._refresh_tables()

    def _cleanup_completed(self) -> None:
        completed = [
            (p, d)
            for p, d in self._autosave_entries
            if d.total_syllables() > 0 and d.synced_syllables() >= d.total_syllables()
        ]
        if not completed:
            QMessageBox.information(self, "Clean Up", "No 100% completed autosaves found to clean up.")
            return
        reply = QMessageBox.question(
            self,
            "Clean Up Completed Autosaves",
            f"Delete {len(completed)} completed autosave{'s' if len(completed) != 1 else ''}?\n(Your saved .kbp and audio files are NOT affected.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for p, _ in completed:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            self._refresh_tables()

    def _open_folder(self) -> None:
        try:
            os.startfile(str(self._autosaves_dir))
        except Exception:
            pass
