from __future__ import annotations

import os
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..tap_sync import TapSyncDraft, default_drafts_dir, list_archived_drafts


class ProjectOrganizerDialog(QDialog):
    """
    Manage background-saved Tap Sync drafts:
    - Reopen any partial/completed draft
    - Export or import portable .zip project bundles (for transferring between people)
    - Delete old drafts or clean up completed ones
    """

    def __init__(self, parent: Optional[QWidget] = None, drafts_dir: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Project Organizer — Saved Drafts & Bundles")
        self.resize(780, 440)
        self._drafts_dir = drafts_dir or default_drafts_dir()
        self._entries: List[Tuple[Path, TapSyncDraft]] = []
        self.selected_draft_path: Optional[Path] = None

        root = QVBoxLayout(self)
        root.setSpacing(8)

        info = QLabel(
            "Background-saved Tap Sync drafts are stored here automatically whenever you work or start a new project. "
            "Export a <b>.zip bundle</b> to transfer a project (including audio & partial sync) to another person."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        root.addWidget(info)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["Project / Song", "Sync Progress", "Audio Files", "Last Saved"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self._open_selected())
        root.addWidget(self._table, 1)

        self._settings = QSettings("MattJoy", "AdvancedKBPEditor")
        row_bundle = QHBoxLayout()
        self._inc_audio_cb = QCheckBox("Include main audio && vocal stem when exporting .zip bundle", self)
        self._inc_audio_cb.setChecked(self._settings.value("bundle_include_audio", True, type=bool))
        self._inc_audio_cb.toggled.connect(self._on_inc_audio_toggled)
        row_bundle.addWidget(self._inc_audio_cb)
        row_bundle.addStretch()
        root.addLayout(row_bundle)

        btns = QHBoxLayout()
        btn_open = QPushButton("Open Draft", self)
        btn_open.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 4px 12px;")
        btn_open.clicked.connect(self._open_selected)
        btns.addWidget(btn_open)

        btn_export = QPushButton("Export Bundle (.zip)…", self)
        btn_export.setToolTip("Package the selected draft (and optional audio) into a portable .zip for sharing")
        btn_export.clicked.connect(self._export_selected)
        btns.addWidget(btn_export)

        btn_import = QPushButton("Import Bundle (.zip)…", self)
        btn_import.setToolTip("Import a shared .zip project bundle and open it")
        btn_import.clicked.connect(self._import_bundle)
        btns.addWidget(btn_import)

        btns.addSpacing(12)
        btn_del = QPushButton("Delete Selected", self)
        btn_del.clicked.connect(self._delete_selected)
        btns.addWidget(btn_del)

        btn_clean = QPushButton("Clean Up Completed", self)
        btn_clean.setToolTip("Delete all drafts that are already 100% synced")
        btn_clean.clicked.connect(self._cleanup_completed)
        btns.addWidget(btn_clean)

        btn_folder = QPushButton("Open Folder", self)
        btn_folder.setToolTip(f"Open drafts folder:\n{self._drafts_dir}")
        btn_folder.clicked.connect(self._open_folder)
        btns.addWidget(btn_folder)

        btns.addStretch()
        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.reject)
        btns.addWidget(btn_close)
        root.addLayout(btns)

        self._refresh_table()

    def _on_inc_audio_toggled(self, checked: bool) -> None:
        self._settings.setValue("bundle_include_audio", bool(checked))
        self._settings.sync()

    def _refresh_table(self) -> None:
        self._entries = list_archived_drafts(self._drafts_dir)
        self._table.setRowCount(len(self._entries))
        for row, (path, draft) in enumerate(self._entries):
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
            self._table.setItem(row, 0, it0)
            self._table.setItem(row, 1, QTableWidgetItem(prog_txt))
            self._table.setItem(row, 2, QTableWidgetItem(aud_txt))
            self._table.setItem(row, 3, QTableWidgetItem(saved_txt))

        if self._entries:
            self._table.selectRow(0)

    def _current_entry(self) -> Optional[Tuple[Path, TapSyncDraft]]:
        row = self._table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _open_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        self.selected_draft_path = entry[0]
        self.accept()

    def _export_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        _, draft = entry
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
            draft, _ = TapSyncDraft.import_bundle_zip(zip_path, target_dir)
            # Also register in local drafts archive so it appears in the Organizer
            archived = draft.save_json(self._drafts_dir / draft.default_archive_filename())
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Could not import project bundle:\n{exc}")
            return
        self.selected_draft_path = archived
        self.accept()

    def _delete_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        path, draft = entry
        reply = QMessageBox.question(
            self,
            "Delete Draft",
            f"Delete saved draft '{draft.display_name}'?\n(Original audio files are NOT deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            self._refresh_table()

    def _cleanup_completed(self) -> None:
        completed = [
            (p, d) for p, d in self._entries
            if d.total_syllables() > 0 and d.synced_syllables() >= d.total_syllables()
        ]
        if not completed:
            QMessageBox.information(self, "Clean Up", "No 100% completed drafts found to clean up.")
            return
        reply = QMessageBox.question(
            self,
            "Clean Up Completed Drafts",
            f"Delete {len(completed)} completed draft{'s' if len(completed) != 1 else ''}?\n(Your saved .kbp and audio files are NOT affected.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for p, _ in completed:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            self._refresh_table()

    def _open_folder(self) -> None:
        try:
            os.startfile(str(self._drafts_dir))
        except Exception:
            pass
