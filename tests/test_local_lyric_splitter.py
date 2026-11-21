from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kbp_editor.local_lyric_splitter import (
    build_lls_cli_command,
    find_local_lyric_splitter_exe,
    read_lls_output_lyrics,
    run_local_lyric_splitter,
)
from kbp_editor.tap_sync import TapSyncDraft


def test_find_local_lyric_splitter_exe_prefers_configured_then_candidates(tmp_path):
    cfg_exe = tmp_path / "custom" / "Local Lyric Splitter.exe"
    cfg_exe.parent.mkdir(parents=True)
    cfg_exe.write_bytes(b"exe")

    default_exe = tmp_path / "default" / "Local Lyric Splitter.exe"
    default_exe.parent.mkdir(parents=True)
    default_exe.write_bytes(b"exe")

    assert find_local_lyric_splitter_exe(str(cfg_exe), candidates=[default_exe]) == cfg_exe
    assert find_local_lyric_splitter_exe(str(tmp_path / "missing.exe"), candidates=[default_exe]) == default_exe
    assert find_local_lyric_splitter_exe("", candidates=[tmp_path / "nope.exe"]) is None


def test_build_lls_cli_command_and_placeholder_audio(tmp_path):
    exe = tmp_path / "Local Lyric Splitter.exe"
    out = tmp_path / "out"

    cmd_default = build_lls_cli_command(exe, "  Queen ", " Bohemian Rhapsody  ", out)
    assert cmd_default == [
        str(exe),
        "--artist",
        "Queen",
        "--song",
        "Bohemian Rhapsody",
        "--audio",
        "placeholder.mp3",
        "--out",
        str(out),
        "--silent",
    ]

    cmd_audio = build_lls_cli_command(exe, "Queen", "Bohemian Rhapsody", out, audio_path=r"C:\music\song.flac")
    assert cmd_audio[cmd_audio.index("--audio") + 1] == r"C:\music\song.flac"


def test_read_lls_output_lyrics_handles_utf8_bom_and_errors(tmp_path):
    with pytest.raises(RuntimeError, match="without generating"):
        read_lls_output_lyrics(tmp_path)

    empty = tmp_path / "empty.txt"
    empty.write_text("   \n\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        read_lls_output_lyrics(tmp_path)

    empty.unlink()
    good = tmp_path / "Artist - Song.txt"
    good.write_text("Hel/lo world\n\nSec/ond page", encoding="utf-8-sig")
    assert read_lls_output_lyrics(tmp_path) == "Hel/lo world\n\nSec/ond page"


def test_run_local_lyric_splitter_success_and_failure(tmp_path, monkeypatch):
    exe = tmp_path / "Local Lyric Splitter.exe"
    exe.write_bytes(b"stub")

    captured = {}

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            self.returncode = 0

        def communicate(self, timeout=None):
            out_dir = Path(captured["cmd"][captured["cmd"].index("--out") + 1])
            (out_dir / "Artist - Song.txt").write_text("Ev/ery/thing is fine\n", encoding="utf-8")
            return ("", "")

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    result = run_local_lyric_splitter(exe, "Artist", "Song")
    assert result == "Ev/ery/thing is fine\n"
    assert captured["cwd"] == str(exe.parent)
    assert "--silent" in captured["cmd"]

    class _FailProc:
        def __init__(self, cmd, **kwargs):
            self.returncode = 1

        def communicate(self, timeout=None):
            return ("", "Error: Lyrics not found")

    monkeypatch.setattr(subprocess, "Popen", _FailProc)
    with pytest.raises(RuntimeError, match="Lyrics not found"):
        run_local_lyric_splitter(exe, "Artist", "Song")


def test_lyrics_input_dialog_lls_flow_and_button_rename(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from kbp_editor.audio import AudioEngine
    from kbp_editor.ui import tap_sync_view as tsv_mod

    app = QApplication.instance() or QApplication([])

    # Verify TapSyncView button is renamed to "Obtain / Enter Lyrics…"
    view = tsv_mod.TapSyncView(AudioEngine())
    try:
        assert view._btn_paste.text() == "Obtain / Enter Lyrics…"
    finally:
        view.close()

    # Use isolated QSettings file
    ini_path = tmp_path / "test_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
    draft = TapSyncDraft(title="Test Song", artist="Test Artist")
    dlg = tsv_mod._LyricsInputDialog(draft, settings=settings)

    try:
        # Simulate default install not found -> prompt -> Cancel returns None without opening file dialog
        fake_exe = tmp_path / "Local Lyric Splitter.exe"
        fake_exe.write_bytes(b"stub")

        monkeypatch.setattr(tsv_mod, "find_local_lyric_splitter_exe", lambda configured_path="": None)
        monkeypatch.setattr(dlg, "_prompt_missing_lls", lambda: "cancel")
        assert dlg._resolve_lls_exe() is None

        # Simulate clicking "Locate .exe…" -> QFileDialog selects exe -> saved to QSettings
        monkeypatch.setattr(dlg, "_prompt_missing_lls", lambda: "locate")
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *args, **kwargs: (str(fake_exe), "Executable Files (*.exe)"),
        )
        resolved = dlg._resolve_lls_exe()
        assert resolved == fake_exe
        assert settings.value(dlg._LLS_SETTINGS_KEY, "", type=str) == str(fake_exe)

        # Missing title/artist shows warning and doesn't launch worker
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
        dlg._artist_edit.setText("")
        dlg._run_local_lyric_splitter()
        assert len(warnings) == 1

        # Running LLS populates text box on success
        dlg._artist_edit.setText("Test Artist")
        monkeypatch.setattr(tsv_mod, "find_local_lyric_splitter_exe", lambda configured_path="": fake_exe)
        monkeypatch.setattr(
            tsv_mod,
            "run_local_lyric_splitter",
            lambda exe_path, artist, song, audio_path="", process_holder=None: f"{artist}/{song}\nSplit/ted",
        )
        dlg._run_local_lyric_splitter()
        assert dlg._lls_worker is not None
        dlg._lls_worker.wait(5000)
        app.processEvents()
        assert dlg.lyrics_text == "Test Artist/Test Song\nSplit/ted"
        assert dlg._lls_btn.isEnabled() is True
    finally:
        dlg.close()
