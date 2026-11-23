from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kbp_editor.chromalyric import (
    ChromaLyricCliOptions,
    build_chromalyric_command,
    find_chromalyric_exe,
    launch_chromalyric,
)
from kbp_editor.model import KbpHeader, KbpProject, Line, Page, Syllable
from kbp_editor.tap_sync import DEFAULT_KBP_HEADER_LINES


def _make_sample_project() -> KbpProject:
    header = KbpHeader.parse(list(DEFAULT_KBP_HEADER_LINES))
    header.set_artist("Test Artist")
    header.set_title("Test Song")
    p1 = Page(lines=[
        Line("C", "A", 100, 350, syllables=[
            Syllable("Hel", 120, 180),
            Syllable("lo ", 180, 240),
            Syllable("world", 240, 300),
        ]),
    ])
    return KbpProject(header=header, pages=[p1])


def test_find_chromalyric_exe_prefers_configured_then_candidates(tmp_path: Path):
    cfg_exe = tmp_path / "custom" / "ChromaLyric.exe"
    cfg_exe.parent.mkdir(parents=True)
    cfg_exe.write_bytes(b"exe")

    default_exe = tmp_path / "default" / "ChromaLyric.exe"
    default_exe.parent.mkdir(parents=True)
    default_exe.write_bytes(b"exe")

    assert find_chromalyric_exe(str(cfg_exe), candidates=[default_exe]) == cfg_exe
    assert find_chromalyric_exe(str(tmp_path / "missing.exe"), candidates=[default_exe]) == default_exe
    assert find_chromalyric_exe("", candidates=[tmp_path / "nope.exe"]) is None


def test_cli_options_only_emit_modified_flags():
    default_opts = ChromaLyricCliOptions()
    assert default_opts.to_cli_args() == []
    assert default_opts.has_modifications is False

    preset_opts = ChromaLyricCliOptions(
        theme_mode="preset",
        preset_name="Neon Synth",
        auto_safe_size=True,
        pseudo_3d=True,
        shadow_layers=7,
    )
    assert preset_opts.has_modifications is True
    assert preset_opts.to_cli_args() == [
        "--apply-preset",
        "Neon Synth",
        "--auto-safe-size",
        "--pseudo-3d",
        "true",
        "--shadow-layers",
        "7",
    ]

    extract_opts = ChromaLyricCliOptions(
        theme_mode="extract",
        extract_image_path=r"C:\art\cover.jpg",
    )
    assert extract_opts.to_cli_args() == ["--extract-theme", r"C:\art\cover.jpg", "--gui"]

    rand_opts = ChromaLyricCliOptions(theme_mode="randomize")
    assert rand_opts.to_cli_args() == ["--randomize"]


def test_build_chromalyric_command_modes(tmp_path: Path):
    exe = tmp_path / "ChromaLyric.exe"
    ass = tmp_path / "song.ass"

    # Unmodified -> plain GUI launch
    cmd, headless = build_chromalyric_command(exe, ass, ChromaLyricCliOptions())
    assert cmd == [str(exe), str(ass)]
    assert headless is False

    # Headless CLI options -> run CLI first, then GUI
    cmd2, headless2 = build_chromalyric_command(
        exe,
        ass,
        ChromaLyricCliOptions(auto_safe_size=True, shadow_layers=4),
    )
    assert cmd2 == [str(exe), str(ass), "--cli", "--out", str(ass), "--auto-safe-size", "--shadow-layers", "4"]
    assert headless2 is True

    # Extract theme includes --gui -> single Popen launch
    cmd3, headless3 = build_chromalyric_command(
        exe,
        ass,
        ChromaLyricCliOptions(theme_mode="extract", extract_image_path="cover.png"),
    )
    assert cmd3 == [str(exe), str(ass), "--cli", "--out", str(ass), "--extract-theme", "cover.png", "--gui"]
    assert headless3 is False


def test_launch_chromalyric_direct_and_headless(tmp_path: Path, monkeypatch):
    exe = tmp_path / "ChromaLyric.exe"
    exe.write_bytes(b"stub")
    ass = tmp_path / "song.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")

    popen_calls = []
    run_calls = []

    def _fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))

    class _FakeCompleted:
        def __init__(self, rc=0, stderr=""):
            self.returncode = rc
            self.stdout = ""
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    # 1. Direct open (no CLI flags)
    msg = launch_chromalyric(exe, ass, None)
    assert "Opened song.ass" in msg
    assert len(run_calls) == 0
    assert popen_calls[-1][0] == [str(exe), str(ass)]

    # 2. Headless CLI + GUI open
    popen_calls.clear()
    msg2 = launch_chromalyric(exe, ass, ChromaLyricCliOptions(theme_mode="randomize"))
    assert "Styled and opened song.ass" in msg2
    assert len(run_calls) == 1
    assert "--randomize" in run_calls[0][0]
    assert popen_calls[-1][0] == [str(exe), str(ass)]

    # 3. CLI failure raises RuntimeError
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _FakeCompleted(1, "Bad preset"))
    with pytest.raises(RuntimeError, match="Bad preset"):
        launch_chromalyric(exe, ass, ChromaLyricCliOptions(theme_mode="randomize"))


def test_finishing_view_chromalyric_flow_and_skip_cli(tmp_path: Path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog
    from kbp_editor.ui import finishing_view as fv_mod

    app = QApplication.instance() or QApplication([])
    ini_path = tmp_path / "finishing_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)

    fake_exe = tmp_path / "ChromaLyric.exe"
    fake_exe.write_bytes(b"stub")

    kbp_path = tmp_path / "Test Artist - Test Song.kbp"
    proj = _make_sample_project()
    proj.save(kbp_path)

    view = fv_mod.FinishingView(settings=settings)
    try:
        view.set_project(proj)
        # Before converting, .ass doesn't exist so ChromaLyric button is disabled
        assert view._open_cl_btn.isEnabled() is False

        # Convert .kbp -> .ass -> ChromaLyric button enables automatically
        view._convert_btn.click()
        assert view._open_cl_btn.isEnabled() is True
        assert view._adv_panel.isHidden() is True
        assert view._skip_cli_btn.isHidden() is True

        # Missing exe -> prompt -> Locate saves to QSettings
        monkeypatch.setattr(fv_mod, "find_chromalyric_exe", lambda configured_path="": None)
        monkeypatch.setattr(view, "_prompt_missing_chromalyric", lambda: "locate")
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *args, **kwargs: (str(fake_exe), "Executable Files (*.exe)"),
        )
        assert view._resolve_chromalyric_exe() == fake_exe
        assert settings.value(view._CL_SETTINGS_KEY, "", type=str) == str(fake_exe)

        # Toggle Advanced Options on & modify a CLI option
        view._adv_toggle_btn.setChecked(True)
        assert not view._adv_panel.isHidden()
        assert not view._skip_cli_btn.isHidden()
        assert view._open_cl_btn.text() == "Open in ChromaLyric"

        view._chk_pseudo_3d.setChecked(True)
        assert view._chk_layers.isChecked() is True
        assert view._open_cl_btn.text() == "Apply CLI && Open in ChromaLyric"

        # Capture launches
        launched = []

        def _fake_launch(exe_path, ass_path, options=None):
            launched.append((exe_path, ass_path, options))
            return "Launched!"

        monkeypatch.setattr(fv_mod, "find_chromalyric_exe", lambda configured_path="": fake_exe)
        monkeypatch.setattr(fv_mod, "launch_chromalyric", _fake_launch)

        # Clicking "Just Open (Skip CLI)" passes options=None even though Pseudo-3D is checked!
        view._skip_cli_btn.click()
        assert view._cl_worker is not None
        view._cl_worker.wait(5000)
        app.processEvents()
        assert len(launched) == 1
        assert launched[0][2] is None

        # Clicking primary "Apply CLI & Open in ChromaLyric" passes the modified options
        view._open_cl_btn.click()
        assert view._cl_worker is not None
        view._cl_worker.wait(5000)
        app.processEvents()
        assert len(launched) == 2
        assert launched[1][2] is not None
        assert launched[1][2].pseudo_3d is True
        assert launched[1][2].shadow_layers == 5
    finally:
        view.close()
        app.processEvents()
