from __future__ import annotations

from pathlib import Path
from kbp_editor.tap_sync import TapSyncDraft, parse_line_to_syllables
from kbp_editor.model import KbpProject


def test_parse_slash_lyrics_and_page_breaks():
    raw = (
        "➣➣➣/The snow has come down\n"
        "on top of ev/ery/thing\n"
        "\n"
        "➣➣➣/True love\n"
        "a/cross a dead-/end street\n"
    )
    draft = TapSyncDraft.from_lyrics_text(raw)
    assert len(draft.pages) == 2
    assert len(draft.pages[0].lines) == 2
    assert len(draft.pages[1].lines) == 2

    l0 = [s.text for s in draft.pages[0].lines[0].syllables]
    assert l0 == ["➣➣➣", "The ", "snow ", "has ", "come ", "down"]

    l1 = [s.text for s in draft.pages[0].lines[1].syllables]
    assert l1 == ["on ", "top ", "of ", "ev", "ery", "thing"]

    l3 = [s.text for s in draft.pages[1].lines[1].syllables]
    assert l3 == ["a", "cross ", "a ", "dead-", "end ", "street"]

    # Round-trip back to slash text
    assert "ev/ery/thing" in draft.to_lyrics_text()
    assert "dead-/end street" in draft.to_lyrics_text()


def test_parse_unsynced_kbp_lyricsv2(tmp_path):
    kbp = tmp_path / "unsynced.kbp"
    kbp.write_bytes(
        "\r\n".join([
            "-----------------------------",
            "KARAOKE BUILDER STUDIO",
            "HEADERV2",
            "'Palette Colours (0-15)",
            "  055,FFF,000,E70,940",
            "  Style00,CustomFont,1,2,3,4",
            "    Verdana,14,B,0",
            "    2,2,2,2,0,0,0,L",
            "  StyleEnd",
            "Title     Test Song",
            "Artist    Test Band",
            "Audio     C:\\music\\test.mp3",
            "-----------------------------",
            "LYRICSV2",
            "Hel/lo world",
            "sec/ond line",
            "",
            "page two line",
            "-----------------------------",
            "",
        ]).encode("utf-8")
    )
    draft = TapSyncDraft.from_kbp_file(kbp)
    assert draft.title == "Test Song"
    assert draft.artist == "Test Band"
    assert draft.audio_path == r"C:\music\test.mp3"
    assert len(draft.pages) == 2
    assert [s.text for s in draft.pages[0].lines[0].syllables] == ["Hel", "lo ", "world"]

    # Building KBP preserves the unsynced file's custom font/header
    proj = draft.build_kbp_project()
    assert proj.header.styles["A"].font_name == "Verdana"
    assert proj.header.styles["A"].font_size == 14


def test_draft_json_roundtrip_and_build_kbp_with_template(tmp_path):
    draft = TapSyncDraft.from_lyrics_text("➣➣➣/Hel/lo world\n\nNext page")
    draft.title = "My Song"
    draft.artist = "My Band"
    draft.audio_path = r"C:\songs\main.flac"
    draft.vocal_path = r"C:\songs\vocals.wav"
    draft.latency_enabled = True
    draft.latency_cs = 5

    # Partially tap Page 1 Line 1 flush
    syls = draft.pages[0].lines[0].syllables
    syls[0].start_cs, syls[0].end_cs = 1000, 1135
    syls[1].start_cs, syls[1].end_cs = 1135, 1180
    syls[2].start_cs, syls[2].end_cs = 1180, 1240
    syls[3].start_cs, syls[3].end_cs = 1240, 1320
    assert draft.pages[0].lines[0].sync_state == "full"
    assert draft.pages[1].lines[0].sync_state == "none"

    # Save & reload .mjkp draft
    jpath = tmp_path / "draft.mjkp"
    draft.save_json(jpath)
    loaded = TapSyncDraft.load_json(jpath)
    assert loaded.audio_path == r"C:\songs\main.flac"
    assert loaded.vocal_path == r"C:\songs\vocals.wav"
    assert loaded.latency_cs == 5
    assert loaded.pages[0].lines[0].syllables[2].start_cs == 1180

    # Create a template KBP with custom style
    tpl = tmp_path / "template.kbp"
    tpl.write_bytes(
        "\r\n".join([
            "-----------------------------",
            "KARAOKE BUILDER STUDIO",
            "HEADERV2",
            "'Palette Colours (0-15)",
            "  000,FFF,000,0F0,050",
            "  Style00,GreenWipe,1,2,3,4",
            "    Tahoma,16,B,0",
            "    3,3,3,3,0,0,0,L",
            "  StyleEnd",
            "Title     Old",
            "Artist    Old",
            "Audio     Old",
            "-----------------------------",
            "PAGEV2",
            "C/A/100/200/0/0/0",
            "x/ 100/150/0",
            "",
        ]).encode("utf-8")
    )
    loaded.template_kbp_path = str(tpl)
    proj = loaded.build_kbp_project()
    assert proj.header.styles["A"].font_name == "Tahoma"
    assert proj.header.title == "My Song"
    assert proj.header.audio_path == r"C:\songs\main.flac"
    assert len(proj.pages) == 2
    assert proj.pages[0].lines[0].syllables[1].start == 1135
    assert proj.pages[0].lines[0].syllables[1].end == 1180
    # Verify roundtrip save/load of the built KBP
    out_kbp = tmp_path / "built.kbp"
    proj.save(out_kbp)
    reloaded = KbpProject.load(out_kbp)
    assert len(reloaded.pages) == 2
    assert reloaded.header.styles["A"].font_name == "Tahoma"


def test_export_and_import_portable_project_bundle(tmp_path):
    main_wav = tmp_path / "src_main.wav"
    voc_wav = tmp_path / "src_vocals.wav"
    main_wav.write_bytes(b"FAKE_MAIN_AUDIO")
    voc_wav.write_bytes(b"FAKE_VOCAL_AUDIO")

    draft = TapSyncDraft.from_lyrics_text("Hel/lo world")
    draft.artist = "Test Artist"
    draft.title = "Test Song"
    draft.audio_path = str(main_wav)
    draft.vocal_path = str(voc_wav)
    draft.pages[0].lines[0].syllables[0].start_cs = 100
    draft.pages[0].lines[0].syllables[0].end_cs = 150

    bundle = tmp_path / "transfer.zip"
    draft.export_bundle_zip(bundle, include_audio=True)
    assert bundle.exists()

    # Simulate another person extracting to a totally different directory
    recv_dir = tmp_path / "other_person" / "Test Song"
    imported, json_path = TapSyncDraft.import_bundle_zip(bundle, recv_dir)
    assert json_path.exists()
    assert imported.display_name == "Test Artist - Test Song"
    assert imported.pages[0].lines[0].syllables[0].start_cs == 100
    assert Path(imported.audio_path).is_file() and Path(imported.audio_path).read_bytes() == b"FAKE_MAIN_AUDIO"
    assert Path(imported.vocal_path).is_file() and Path(imported.vocal_path).read_bytes() == b"FAKE_VOCAL_AUDIO"


def _write_synced_kbp(path: Path, title: str = "Synced Song") -> Path:
    path.write_bytes(
        "\r\n".join([
            "-----------------------------",
            "KARAOKE BUILDER STUDIO",
            "HEADERV2",
            "'Palette Colours (0-15)",
            "  055,FFF,000,E70,940",
            "  Style00,Default,1,2,3,4",
            "    Arial,12,B,0",
            "    2,2,2,2,0,0,0,L",
            "  StyleEnd",
            f"Title     {title}",
            "Artist    Synced Band",
            "Audio     ",
            "-----------------------------",
            "PAGEV2",
            "C/A/100/400/0/0/0",
            "Hel/ 150/200/0",
            "lo/ 200/280/0",
            "world/ 280/360/0",
            "",
        ]).encode("utf-8")
    )
    return path


def _write_unsynced_kbp(path: Path, title: str = "Unsynced Song") -> Path:
    path.write_bytes(
        "\r\n".join([
            "-----------------------------",
            "KARAOKE BUILDER STUDIO",
            "HEADERV2",
            "'Palette Colours (0-15)",
            "  055,FFF,000,E70,940",
            "  Style00,Default,1,2,3,4",
            "    Arial,12,B,0",
            "    2,2,2,2,0,0,0,L",
            "  StyleEnd",
            f"Title     {title}",
            "Artist    Unsynced Band",
            "Audio     ",
            "-----------------------------",
            "LYRICSV2",
            "Other/ song lyr/ics",
            "-----------------------------",
            "",
        ]).encode("utf-8")
    )
    return path


def test_from_kbp_project_preserves_timings_and_header(tmp_path):
    kbp_path = _write_synced_kbp(tmp_path / "synced.kbp")
    proj = KbpProject.load(kbp_path)
    draft = TapSyncDraft.from_kbp_project(proj)
    assert draft.title == "Synced Song"
    assert draft.artist == "Synced Band"
    assert draft.total_syllables() == 3
    assert draft.synced_syllables() == 3
    assert draft.pages[0].lines[0].syllables[0].start_cs == 150
    assert draft.pages[0].lines[0].syllables[2].end_cs == 360
    assert draft.raw_header_lines is not None


def test_synced_kbp_locks_tap_sync_and_blocks_unsynced_import(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from kbp_editor.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    synced_path = _write_synced_kbp(tmp_path / "synced.kbp")
    unsynced_path = _write_unsynced_kbp(tmp_path / "unsynced.kbp")

    win = MainWindow()
    try:
        win.open_kbp(synced_path)
        assert win.project is not None
        assert win.tap_sync_view.is_locked is True
        assert win.tap_sync_view.has_synced_project is True
        assert win.tap_sync_view.draft.title == "Synced Song"
        assert win.tap_sync_view.draft.synced_syllables() == 3
        assert win.tap_sync_view._sync_btn.isEnabled() is False
        assert win.tap_sync_view._btn_load_lyr.isEnabled() is False
        assert win.tap_sync_view._resume_btn.isEnabled() is True

        # Attempting to open another .kbp (unsynced or synced) while a project is open
        # automatically opens it in a new window without modifying the current window
        launched = []
        monkeypatch.setattr(
            win,
            "_launch_new_window",
            lambda target=None: (launched.append(Path(target) if target else None), True)[1],
        )
        win.open_kbp(unsynced_path)
        assert launched == [unsynced_path]
        assert win.tap_sync_view.draft.title == "Synced Song"
        assert win.project.header.title == "Synced Song"
        assert not hasattr(win, "_recent_menu")

        # Opening Project Organizer while a synced .kbp is loaded (and Tap Sync is frozen/inactive)
        # works and opens the selected project in a new window
        from PySide6.QtWidgets import QDialog
        from kbp_editor.ui import tap_sync_view as tsv_mod

        opened_parents = []

        class _FakeOrganizer:
            def __init__(self, parent=None):
                opened_parents.append(parent)
                self.selected_project_path = unsynced_path

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(tsv_mod, "ProjectOrganizerDialog", _FakeOrganizer)
        launched.clear()
        win._open_project_organizer()
        assert opened_parents == [win]
        assert launched == [unsynced_path]
        assert win.project.header.title == "Synced Song"

        # Unfreezing allows re-tapping the synced project; Project Organizer stays accessible
        win.tap_sync_view.set_locked(False)
        assert win.tap_sync_view.is_locked is False
        assert win.tap_sync_view._sync_btn.isEnabled() is True
        assert win.tap_sync_view._btn_retap.isEnabled() is True
        assert win.tap_sync_view._build_btn.isEnabled() is True
        assert win.tap_sync_view._btn_load_lyr.isEnabled() is False
        assert win.tap_sync_view._resume_btn.isEnabled() is True

        # Saving re-locks Tap Sync
        win.save_kbp()
        assert win.tap_sync_view.is_locked is True

        # Starting a fresh project clears synced project and unlocks Tap Sync
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        win.new_project()
        assert win.project is None
        assert win.tap_sync_view.is_locked is False
        assert win.tap_sync_view.has_synced_project is False
        assert win.tap_sync_view._btn_load_lyr.isEnabled() is True
    finally:
        win.close()
        app.processEvents()


def test_close_prompts_for_unsaved_lyrics_and_tap_sync_changes(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    from kbp_editor.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    synced_path = _write_synced_kbp(tmp_path / "synced_close.kbp")
    unsynced_path = _write_unsynced_kbp(tmp_path / "unsynced_close.kbp")

    win = MainWindow()
    try:
        # 1) Unsaved Lyrics Wiping edit -> Cancel blocks close, Save saves & allows close
        win.open_kbp(synced_path)
        assert not win._has_unsaved_changes()
        win.project.shift_all(10)
        win._on_project_mutated("Shift +10 cs")
        assert win.history.is_dirty is True
        assert win.windowTitle().startswith("*")

        prompts = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *a, **kw: (prompts.append(a[2]), QMessageBox.StandardButton.Cancel)[1],
        )
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert ev.isAccepted() is False
        assert len(prompts) == 1

        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *a, **kw: (prompts.append(a[2]), QMessageBox.StandardButton.Save)[1],
        )
        ev2 = QCloseEvent()
        win.closeEvent(ev2)
        assert ev2.isAccepted() is True
        assert win.history.is_dirty is False
        assert KbpProject.load(synced_path).pages[0].lines[0].syllables[0].start == 160

        # 2) Unsaved Tap Sync edit on an unsynced draft -> Cancel blocks close, Discard allows close
        win.new_project()
        win.open_kbp(unsynced_path)
        assert win.tap_sync_view.is_dirty is False
        win.tap_sync_view._clear_selected_line()
        assert win.tap_sync_view.is_dirty is True
        assert win.windowTitle().startswith("*")

        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *a, **kw: QMessageBox.StandardButton.Cancel,
        )
        ev3 = QCloseEvent()
        win.closeEvent(ev3)
        assert ev3.isAccepted() is False

        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *a, **kw: QMessageBox.StandardButton.Discard,
        )
        ev4 = QCloseEvent()
        win.closeEvent(ev4)
        assert ev4.isAccepted() is True
    finally:
        win.tap_sync_view.mark_clean()
        win.history.mark_saved()
        win.close()
        app.processEvents()


def test_project_organizer_multi_delete_and_skip_confirm(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget
    from kbp_editor.tap_sync import register_saved_project
    from kbp_editor.ui.project_organizer import ProjectOrganizerDialog

    app = QApplication.instance() or QApplication([])
    autosaves_dir = tmp_path / "autosaves"
    autosaves_dir.mkdir()
    reg_path = tmp_path / "saved_projects.json"

    saved_kbp = _write_synced_kbp(tmp_path / "explicit.kbp", title="Explicitly Saved")
    register_saved_project(saved_kbp, registry_path=reg_path)

    for name in ("Song A", "Song B", "Song C"):
        d = TapSyncDraft.from_lyrics_text("Hel/lo world")
        d.title = name
        d.save_json(autosaves_dir / d.default_archive_filename())

    dlg = ProjectOrganizerDialog(drafts_dir=autosaves_dir, registry_path=reg_path)
    orig_confirm = dlg._settings.value("confirm_delete_draft", True, type=bool)
    try:
        # Saved Projects tab shows only explicitly saved projects; Autosaves tab shows autosaves
        assert dlg._saved_table.selectionMode() == QTableWidget.SelectionMode.ExtendedSelection
        assert dlg._autosave_table.selectionMode() == QTableWidget.SelectionMode.ExtendedSelection
        assert dlg._saved_table.rowCount() == 1
        assert dlg._autosave_table.rowCount() == 3

        # Switch to Autosaves tab, enable confirmation, check "Do not remind me again" and delete 2 rows at once
        dlg._tabs.setCurrentWidget(dlg._autosave_table)
        dlg._settings.setValue("confirm_delete_draft", True)
        dlg._confirm_del_cb.setChecked(True)
        sel = dlg._autosave_table.selectionModel()
        sel.clearSelection()
        sel.select(dlg._autosave_table.model().index(0, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        sel.select(dlg._autosave_table.model().index(2, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        assert len(dlg._selected_entries()) == 2

        exec_calls = []

        def _fake_exec(msg_self):
            exec_calls.append(msg_self.text())
            cb = msg_self.checkBox()
            assert cb is not None and cb.text() == "Do not remind me again"
            cb.setChecked(True)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
        dlg._delete_selected()
        assert len(exec_calls) == 1
        assert "2 selected autosaves" in exec_calls[0]
        assert dlg._autosave_table.rowCount() == 1
        assert dlg._confirm_del_cb.isChecked() is False

        # Deleting the remaining autosave should NOT show the popup again
        dlg._autosave_table.selectRow(0)
        dlg._delete_selected()
        assert len(exec_calls) == 1
        assert dlg._autosave_table.rowCount() == 0

        # Removing from Saved Projects removes from registry without deleting the .kbp on disk
        dlg._tabs.setCurrentWidget(dlg._saved_table)
        dlg._saved_table.selectRow(0)
        dlg._delete_selected()
        assert dlg._saved_table.rowCount() == 0
        assert saved_kbp.exists()
    finally:
        dlg._settings.setValue("confirm_delete_draft", orig_confirm)
        dlg._settings.sync()
        dlg.close()
        app.processEvents()


def test_default_kbp_comments_exact_format_and_preserve(tmp_path):
    # 1) Freshly built KBP from TapSyncDraft gets the exact 10-column KBS Comments block
    draft = TapSyncDraft.from_lyrics_text("Hel/lo world")
    draft.pages[0].lines[0].syllables[0].start_cs = 100
    draft.pages[0].lines[0].syllables[0].end_cs = 150
    proj = draft.build_kbp_project()
    raw_bytes = proj.to_bytes()
    assert b"Comments  Hacked by Matt Joy\r\n          github.com/mattjoykaraoke\r\n\r\n-----------------------------\r\nPAGEV2\r\n" in raw_bytes
    assert proj.header.comments == ["Hacked by Matt Joy", "github.com/mattjoykaraoke"]

    # 2) Unsynced KBP with blank Comments field also gets the default comments on build
    unsynced = _write_unsynced_kbp(tmp_path / "blank_comments.kbp")
    draft2 = TapSyncDraft.from_kbp_file(unsynced)
    proj2 = draft2.build_kbp_project()
    assert b"Comments  Hacked by Matt Joy\r\n          github.com/mattjoykaraoke\r\n" in proj2.to_bytes()

    # 3) Existing custom multi-line comments are parsed and preserved verbatim
    proj.header.set_comments(["Custom Line 1", "Custom Line 2", "Tag: v1"])
    out = tmp_path / "custom_comments.kbp"
    proj.save(out)
    reloaded = KbpProject.load(out)
    assert reloaded.header.comments == ["Custom Line 1", "Custom Line 2", "Tag: v1"]
    reloaded.header.ensure_default_comments()
    assert reloaded.header.comments == ["Custom Line 1", "Custom Line 2", "Tag: v1"]
    assert b"Comments  Custom Line 1\r\n          Custom Line 2\r\n          Tag: v1\r\n" in reloaded.to_bytes()


def test_unified_save_works_for_unsynced_tap_sync_project(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog
    from kbp_editor.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    unsynced_path = _write_unsynced_kbp(tmp_path / "partial.kbp")
    saved_draft = tmp_path / "partial.mjkp"

    win = MainWindow()
    try:
        assert win._act_save.isEnabled() is False
        assert win._act_save_as.isEnabled() is False

        win.open_kbp(unsynced_path)
        assert win.project is None
        assert bool(win.tap_sync_view.draft.pages) is True
        assert win._act_save.isEnabled() is True
        assert win._act_save_as.isEnabled() is True

        captured_default = []

        def _fake_save(parent, caption, directory, filter_str):
            captured_default.append(directory)
            return (str(saved_draft), "")

        monkeypatch.setattr(QFileDialog, "getSaveFileName", _fake_save)
        assert win.save_kbp() is True
        assert captured_default == ["Unsynced Band - Unsynced Song.mjkp"]
        assert saved_draft.exists()
        assert win.tap_sync_view.is_dirty is False
    finally:
        win.close()
        app.processEvents()


def test_teleprompter_wheel_scrolls_lines_and_resets_on_start_sync():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from kbp_editor.audio import AudioEngine
    from kbp_editor.ui.tap_sync_view import TapSyncView

    app = QApplication.instance() or QApplication([])
    view = TapSyncView(AudioEngine())
    try:
        draft = TapSyncDraft.from_lyrics_text(
            "Line one syl/la/bles\nLine two here\n\nLine three page two\nLine four end"
        )
        view._apply_draft(draft, load_audio=False)
        tp = view._teleprompter
        assert tp.preview_flat_idx is None
        assert view._cursor == (0, 0, 0)

        def _wheel(dy: int) -> None:
            ev = QWheelEvent(
                QPointF(100.0, 100.0),
                QPointF(100.0, 100.0),
                QPoint(0, 0),
                QPoint(0, dy),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            tp.wheelEvent(ev)

        # Scroll down 3 notches -> line indices 1, 2, 3 (clamped at 3)
        _wheel(-120)
        assert tp.preview_flat_idx == 1
        _wheel(-120)
        assert tp.preview_flat_idx == 2
        _wheel(-120)
        assert tp.preview_flat_idx == 3
        _wheel(-120)
        assert tp.preview_flat_idx == 3

        # Scroll back up 1 notch -> index 2
        _wheel(120)
        assert tp.preview_flat_idx == 2
        # Cursor itself was never displaced
        assert view._cursor == (0, 0, 0)

        # Starting sync resets preview scroll back to the beginning/cursor
        view.start_syncing()
        assert tp.preview_flat_idx is None
        assert view._cursor == (0, 0, 0)
        view.stop_syncing()
    finally:
        view.close()
        app.processEvents()


def test_drag_and_drop_mjkp_loads_project_and_updates_organizer(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication
    from kbp_editor.tap_sync import list_saved_projects
    from kbp_editor.ui import tap_sync_view as tsv_mod
    from kbp_editor.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    reg_path = tmp_path / "saved_projects.json"
    monkeypatch.setattr(
        tsv_mod,
        "register_saved_project",
        lambda p, registry_path=None: __import__("kbp_editor.tap_sync", fromlist=["register_saved_project"]).register_saved_project(p, registry_path=reg_path),
    )

    draft = TapSyncDraft.from_lyrics_text("Drop/ped lyr/ics")
    draft.artist = "Drop Band"
    draft.title = "Drop Song"
    mjkp_path = tmp_path / "Drop Band - Drop Song.mjkp"
    draft.save_json(mjkp_path)

    win = MainWindow()
    try:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(mjkp_path))])

        enter_ev = QDragEnterEvent(
            QPointF(10, 10).toPoint(),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        win.dragEnterEvent(enter_ev)
        assert enter_ev.isAccepted() is True

        drop_ev = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        win.dropEvent(drop_ev)
        assert drop_ev.isAccepted() is True
        assert win.tap_sync_view.draft.artist == "Drop Band"
        assert win.tap_sync_view.draft.title == "Drop Song"
        assert win._tabs.currentWidget() is win.tap_sync_view
        assert [p.resolve() for p in list_saved_projects(reg_path)] == [mjkp_path.resolve()]
    finally:
        win.close()
        app.processEvents()

    # Also silently accepts .txt drag-and-drop into a fresh window
    txt_path = tmp_path / "Txt Artist - Txt Song.txt"
    txt_path.write_text("Hel/lo world\n\nSec/ond page", encoding="utf-8")
    win2 = MainWindow()
    try:
        mime2 = QMimeData()
        mime2.setUrls([QUrl.fromLocalFile(str(txt_path))])
        drop_ev2 = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime2,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        win2.dropEvent(drop_ev2)
        assert drop_ev2.isAccepted() is True
        assert win2.tap_sync_view.draft.artist == "Txt Artist"
        assert win2.tap_sync_view.draft.title == "Txt Song"
        assert win2.tap_sync_view.draft.total_syllables() == 6
    finally:
        win2.tap_sync_view.mark_clean()
        win2.close()
        app.processEvents()


def test_tap_sync_direct_coord_stepping_and_preview_cache_retention(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from kbp_editor.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    synced_path = _write_synced_kbp(tmp_path / "cache_test.kbp")
    win = MainWindow()
    try:
        win.open_kbp(synced_path)
        draft = TapSyncDraft.from_lyrics_text("One/ two\nThree\n\nFour/ five")
        tsv = win.tap_sync_view
        tsv._apply_draft(draft, load_audio=False)

        # Verify allocation-free _next_coord and _prev_coord across line and page boundaries
        coords = []
        cur = (0, 0, 0)
        while cur is not None:
            coords.append(cur)
            cur = tsv._next_coord(cur)
        assert coords == [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0), (1, 0, 1)]

        rev = []
        cur = coords[-1]
        while cur is not None:
            rev.append(cur)
            cur = tsv._prev_coord(cur)
        assert rev == list(reversed(coords))

        # Populate PreviewCanvas line cache and WipingTimelineCanvas sublane cache
        win.preview_win.canvas.set_time_cs(200.0)
        img = QImage(512, 288, QImage.Format.Format_ARGB32_Premultiplied)
        win.preview_win.canvas.render(img)
        assert len(win.preview_win.canvas._line_cache) > 0

        # Timing-only mutation in MainWindow preserves PreviewCanvas 1080p text pixmap cache
        win.project.pages[0].lines[0].syllables[0].end += 5
        win._on_project_mutated("Nudge Syllable")
        assert len(win.preview_win.canvas._line_cache) > 0

        # WipingTimelineCanvas sublane layout cache builds on demand and invalidates on mutation
        style_order, sublanes = win.wiping_view.canvas._ensure_sublane_layout()
        assert "A" in style_order and len(sublanes["A"]) == 1
        win._on_project_mutated("Second Nudge")
        assert win.wiping_view.canvas._sublane_cache is None
    finally:
        win.tap_sync_view.mark_clean()
        win.history.mark_saved()
        win.close()
        app.processEvents()

