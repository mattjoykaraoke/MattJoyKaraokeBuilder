from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kbp_editor.model import KbpHeader, KbpProject, Line, Page, Syllable
from kbp_editor.tap_sync import DEFAULT_KBP_HEADER_LINES
from kbp_editor.video_export import (
    VideoStreamInfo,
    build_ffmpeg_video_command,
    build_title_card_clip_command,
    check_ass_should_be_transparent,
    find_ffmpeg_exe,
    parse_ass_background_color,
    prepend_title_card,
    render_karaoke_video,
)


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


def test_find_ffmpeg_exe_supports_file_and_dir(tmp_path: Path):
    bin_dir = tmp_path / "ffmpeg" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "ffmpeg.exe"
    exe.write_bytes(b"stub")

    assert find_ffmpeg_exe(str(exe), candidates=[]) == exe
    assert find_ffmpeg_exe(str(bin_dir), candidates=[]) == exe
    assert find_ffmpeg_exe("", candidates=[exe]) == exe
    assert find_ffmpeg_exe("", candidates=[tmp_path / "missing.exe"]) is None


def test_parse_ass_background_color_and_transparency(tmp_path: Path):
    opaque_ass = tmp_path / "opaque.ass"
    opaque_ass.write_text(
        "[Script Info]\n"
        "; kbputils_background_1.0 color: #FF112233\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000088FF,&H00000000,&H80000000\n",
        encoding="utf-8-sig",
    )
    assert parse_ass_background_color(opaque_ass) == "#112233"
    assert check_ass_should_be_transparent(opaque_ass) is False

    trans_ass = tmp_path / "trans.ass"
    trans_ass.write_text(
        "[Script Info]\n"
        "; kbputils_background_1.0 color: #00000000\n",
        encoding="utf-8-sig",
    )
    assert parse_ass_background_color(trans_ass) == "#000000"
    assert check_ass_should_be_transparent(trans_ass) is True


def test_build_ffmpeg_video_command_mp4_and_mov():
    # 1. MP4 with NVENC
    cmd_nv = build_ffmpeg_video_command(
        "ffmpeg.exe",
        r"C:\song\inst.flac",
        r"C:\song\temp_render_subs.ass",
        r"C:\song\out.mp4",
        "#112233",
        180.5,
        transparent=False,
        use_nvenc=True,
    )
    assert "-c:v" in cmd_nv and "h264_nvenc" in cmd_nv
    assert "-c:a" in cmd_nv and "copy" in cmd_nv
    assert "color=color=112233:d=180.5:r=60:s=1920x1080[s0];[s0]ass=temp_render_subs.ass[s1]" in cmd_nv
    assert cmd_nv[-1] == r"C:\song\out.mp4"

    # 2. MP4 with libx264
    cmd_cpu = build_ffmpeg_video_command(
        "ffmpeg.exe",
        r"C:\song\inst.mp3",
        r"C:\song\temp_render_subs.ass",
        r"C:\song\out.mp4",
        "#000000",
        120.0,
        transparent=False,
        use_nvenc=False,
    )
    assert "libx264" in cmd_cpu and "stillimage" in cmd_cpu

    # 3. Transparent MOV (ProRes Alpha) auto-switches .mp4 -> .mov
    cmd_mov = build_ffmpeg_video_command(
        "ffmpeg.exe",
        r"C:\song\inst.wav",
        r"C:\song\temp_render_subs.ass",
        r"C:\song\out.mp4",
        "#000000",
        95.0,
        transparent=True,
        use_nvenc=False,
    )
    assert "prores" in cmd_mov and "yuva444p10le" in cmd_mov and "pcm_s24le" in cmd_mov
    assert "alpha=1[s1]" in cmd_mov[cmd_mov.index("-filter_complex") + 1]
    assert cmd_mov[-1].endswith(".mov")


def test_render_karaoke_video_nvenc_fallback_to_libx264(tmp_path: Path, monkeypatch):
    from kbp_editor import video_export as ve

    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"stub")
    ass = tmp_path / "song.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    audio = tmp_path / "inst.mp3"
    audio.write_bytes(b"audio")
    out = tmp_path / "song.mp4"

    monkeypatch.setattr(ve, "get_audio_duration", lambda *a, **kw: 10.0)
    monkeypatch.setattr(ve, "has_nvenc_support", lambda *a, **kw: True)

    seen_cmds = []

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            seen_cmds.append(cmd)
            self._cmd = cmd
            self.stdout = iter(["frame=100 time=00:00:05.00\n"])
            # Fail NVENC pass, succeed libx264 pass
            self.returncode = 1 if "h264_nvenc" in cmd else 0

        def wait(self):
            if self.returncode == 0:
                Path(self._cmd[-1]).write_bytes(b"FAKE_MP4_DATA")
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)

    saved = render_karaoke_video(exe, ass, audio, out, "#000000", transparent=False)
    assert saved == out
    assert out.is_file() and out.read_bytes() == b"FAKE_MP4_DATA"
    assert len(seen_cmds) == 2
    assert "h264_nvenc" in seen_cmds[0]
    assert "libx264" in seen_cmds[1]
    assert not (tmp_path / "temp_render_subs.ass").exists()


def test_finishing_view_make_video_wiring(tmp_path: Path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from kbp_editor.ui import finishing_view as fv_mod

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "vid_settings.ini"), QSettings.Format.IniFormat)

    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"stub")
    inst_audio = tmp_path / "instrumental.flac"
    inst_audio.write_bytes(b"audio")

    kbp_path = tmp_path / "Test Artist - Test Song.kbp"
    proj = _make_sample_project()
    proj.save(kbp_path)

    view = fv_mod.FinishingView(settings=settings)
    try:
        view.set_project(proj)
        assert view._make_video_btn.isEnabled() is False

        # Converting .kbp -> .ass auto-populates the Make Video .ass and Output Video fields
        view._convert_btn.click()
        expected_ass = tmp_path / "Test Artist - Test Song.ass"
        assert Path(view._vid_ass_edit.text()) == expected_ass
        assert Path(view._vid_out_edit.text()) == tmp_path / "Test Artist - Test Song.mp4"
        assert view._make_video_btn.isEnabled() is False  # still waiting for instrumental audio

        # Selecting instrumental audio enables Make Video (MP4)
        view._vid_audio_edit.setText(str(inst_audio))
        assert view._make_video_btn.isEnabled() is True
        assert view._make_video_btn.text() == "Make Video (MP4)"

        # Toggling Transparent BG switches output extension to .mov and button label to MOV
        view._vid_transparent_cb.setChecked(True)
        assert Path(view._vid_out_edit.text()) == tmp_path / "Test Artist - Test Song.mov"
        assert view._make_video_btn.text() == "Make Video (MOV)"

        # Switching to a custom "Save As" .ass file in another folder updates default output folder
        alt_dir = tmp_path / "styled"
        alt_dir.mkdir()
        alt_ass = alt_dir / "Custom Styled.ass"
        alt_ass.write_text("; kbputils_background_1.0 color: #FF224466\n", encoding="utf-8-sig")
        view._set_video_ass_path(alt_ass)
        assert view._vid_bg_edit.text() == "#224466"
        assert view._vid_transparent_cb.isChecked() is False
        assert Path(view._vid_out_edit.text()) == alt_dir / "Custom Styled.mp4"

        # Externally modifying the watched .ass file (e.g. changing BG color or alpha in ChromaLyric)
        # automatically updates the background color, transparency toggle, and output extension
        alt_ass.write_text("; kbputils_background_1.0 color: #00AABBCC\n; extra\n", encoding="utf-8-sig")
        view._check_watched_ass_changed()
        assert view._vid_bg_edit.text() == "#AABBCC"
        assert view._vid_transparent_cb.isChecked() is True
        assert Path(view._vid_out_edit.text()) == alt_dir / "Custom Styled.mov"

        # Changing it back to opaque restores .mp4 and new color
        alt_ass.write_text("; kbputils_background_1.0 color: #FF224466\n; opaque again\n", encoding="utf-8-sig")
        view._check_watched_ass_changed()
        assert view._vid_bg_edit.text() == "#224466"
        assert view._vid_transparent_cb.isChecked() is False
        assert Path(view._vid_out_edit.text()) == alt_dir / "Custom Styled.mp4"

        # Clicking Make Video invokes render_karaoke_video in background worker
        rendered = []

        def _fake_render(ffmpeg_exe, ass_path, audio_path, output_path, bg_color, **kwargs):
            rendered.append((ffmpeg_exe, ass_path, audio_path, output_path, bg_color, kwargs.get("transparent")))
            Path(output_path).write_bytes(b"MP4")
            return Path(output_path)

        monkeypatch.setattr(fv_mod, "find_ffmpeg_exe", lambda configured_path="": fake_ffmpeg)
        monkeypatch.setattr(fv_mod, "render_karaoke_video", _fake_render)

        view._make_video_btn.click()
        assert view._video_worker is not None
        view._video_worker.wait(5000)
        app.processEvents()

        assert len(rendered) == 1
        assert rendered[0][1] == alt_ass
        assert rendered[0][2] == inst_audio
        assert rendered[0][3] == alt_dir / "Custom Styled.mp4"
        assert rendered[0][4] == "#224466"
        assert rendered[0][5] is False
        assert not view._open_video_folder_btn.isHidden()

        # Make Video completion auto-populates Prepend Title Card video & output fields
        assert Path(view._tc_video_edit.text()) == alt_dir / "Custom Styled.mp4"
        assert Path(view._tc_out_edit.text()) == alt_dir / "Custom Styled (with Title Card).mp4"
        assert view._prepend_tc_btn.isEnabled() is False

        title_img = tmp_path / "title_card.png"
        title_img.write_bytes(b"PNG")
        view._tc_img_edit.setText(str(title_img))
        view._tc_dur_spin.setValue(4.5)
        assert view._prepend_tc_btn.isEnabled() is True

        prepended = []

        def _fake_prepend(ffmpeg_exe, video_path, image_path, output_path, duration_sec=3.0, **kwargs):
            prepended.append((video_path, image_path, output_path, duration_sec))
            Path(output_path).write_bytes(b"FINAL_MP4")
            return Path(output_path)

        monkeypatch.setattr(fv_mod, "prepend_title_card", _fake_prepend)
        view._prepend_tc_btn.click()
        assert view._tc_worker is not None
        view._tc_worker.wait(5000)
        app.processEvents()

        assert len(prepended) == 1
        assert prepended[0][0] == alt_dir / "Custom Styled.mp4"
        assert prepended[0][1] == title_img
        assert prepended[0][2] == alt_dir / "Custom Styled (with Title Card).mp4"
        assert prepended[0][3] == 4.5
        assert not view._open_tc_folder_btn.isHidden()
    finally:
        view.close()
        app.processEvents()


def test_build_title_card_clip_and_prepend_stream_copy(tmp_path: Path, monkeypatch):
    from kbp_editor import video_export as ve

    info = VideoStreamInfo(
        width=1920,
        height=1080,
        fps="60/1",
        vcodec="h264",
        pix_fmt="yuv420p",
        video_timescale=15360,
        acodec="mp3",
        sample_rate=44100,
        channels=2,
        channel_layout="stereo",
        has_audio=True,
    )
    clip_cmd = build_title_card_clip_command("ffmpeg.exe", "card.png", 3.5, "intro.mp4", info)
    assert "-video_track_timescale" in clip_cmd and "15360" in clip_cmd
    assert "libmp3lame" in clip_cmd
    assert "3.500" in clip_cmd

    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"stub")
    vid = tmp_path / "song.mp4"
    vid.write_bytes(b"SONG")
    img = tmp_path / "card.png"
    img.write_bytes(b"IMG")
    out = tmp_path / "song_final.mp4"

    monkeypatch.setattr(ve, "probe_video_streams", lambda *a, **kw: info)
    calls = []

    class _Done:
        def __init__(self, rc=0):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"DATA")
        return _Done(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    saved = prepend_title_card(exe, vid, img, out, duration_sec=4.0)
    assert saved == out
    assert out.is_file() and out.read_bytes() == b"DATA"
    assert len(calls) == 2
    assert "-c" in calls[1] and "copy" in calls[1]


def test_finishing_view_detects_existing_video_in_ass_directory(tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from kbp_editor.ui import finishing_view as fv_mod

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "detect_vid_settings.ini"), QSettings.Format.IniFormat)

    # Unsaved project (source_path is None), but all files already exist in a folder
    song_dir = tmp_path / "my_song"
    song_dir.mkdir()
    ass_path = song_dir / "Test Artist - Test Song.ass"
    ass_path.write_text("; kbputils_background_1.0 color: #FF112233\n", encoding="utf-8-sig")
    existing_mp4 = song_dir / "Test Artist - Test Song.mp4"
    existing_mp4.write_bytes(b"EXISTING_MP4")
    existing_tc = song_dir / "Test Artist - Test Song (with Title Card).mp4"
    existing_tc.write_bytes(b"EXISTING_TC_MP4")

    unsaved_proj = _make_sample_project()
    assert unsaved_proj.source_path is None

    view = fv_mod.FinishingView(settings=settings)
    try:
        view.set_project(unsaved_proj)
        assert view._last_video_path is None
        assert view._open_video_folder_btn.isHidden()

        # Selecting the .ass file in Make Video detects the completed video in its directory
        view._set_video_ass_path(ass_path)
        assert view._last_video_path == existing_mp4
        assert not view._open_video_folder_btn.isHidden()
        assert not view._vid_status_lbl.isHidden()
        assert existing_mp4.name in view._vid_status_lbl.text()
        assert Path(view._tc_video_edit.text()) == existing_mp4
        assert Path(view._tc_out_edit.text()) == existing_tc
        assert view._open_cl_btn.isEnabled() is True

        # Styled .ass variant in the same folder also finds the base video (and skips title-card video)
        styled_ass = song_dir / "Test Artist - Test Song_styled.ass"
        styled_ass.write_text("; kbputils_background_1.0 color: #FF112233\n", encoding="utf-8-sig")
        view._set_video_ass_path(styled_ass)
        assert view._last_video_path == existing_mp4
        assert Path(view._tc_video_edit.text()) == existing_mp4

        # Shared folder with another song's video must NOT falsely match an unrelated .ass
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "Other Song.mp4").write_bytes(b"OTHER")
        unrendered_ass = shared_dir / "Brand New Song.ass"
        unrendered_ass.write_text("; kbputils_background_1.0 color: #FF000000\n", encoding="utf-8-sig")
        view._set_video_ass_path(unrendered_ass)
        assert view._last_video_path is None
        assert view._open_video_folder_btn.isHidden()
        assert view._vid_status_lbl.isHidden()
    finally:
        view.close()
        app.processEvents()


