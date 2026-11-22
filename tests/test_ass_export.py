from __future__ import annotations

from pathlib import Path

from kbp_editor.ass_export import (
    OVERFLOW_EVEN_SPLIT,
    OVERFLOW_NO_WRAP,
    AssExportOptions,
    convert_kbp_file_to_ass,
    convert_project_to_ass,
    suggest_ass_path,
)
from kbp_editor.model import KbpHeader, KbpProject, Line, Page, Syllable
from kbp_editor.tap_sync import DEFAULT_KBP_HEADER_LINES


def _make_valid_kbp_project(artist: str = "Test Artist", title: str = "Test Song") -> KbpProject:
    raw = []
    for ln in DEFAULT_KBP_HEADER_LINES:
        if ln.startswith("Title"):
            raw.append(f"Title     {title}")
        elif ln.startswith("Artist"):
            raw.append(f"Artist    {artist}")
        else:
            raw.append(ln)
    header = KbpHeader.parse(raw)
    p1 = Page(lines=[
        Line("C", "A", 100, 350, syllables=[
            Syllable("Hel", 120, 180),
            Syllable("lo ", 180, 240),
            Syllable("world", 240, 300),
        ]),
    ])
    return KbpProject(header=header, pages=[p1])


def test_suggest_ass_path_prefers_source_path(tmp_path: Path):
    proj = _make_valid_kbp_project("Blink 182", "All The Small Things")
    kbp_file = tmp_path / "custom_name.kbp"
    proj.source_path = kbp_file

    assert suggest_ass_path(proj) == tmp_path / "custom_name.ass"


def test_suggest_ass_path_from_header_metadata(tmp_path: Path):
    proj = _make_valid_kbp_project("Artist: One", "Song? Two")
    proj.source_path = None

    assert suggest_ass_path(proj, fallback_dir=tmp_path) == tmp_path / "Artist One - Song Two.ass"


def test_convert_project_to_ass_writes_valid_1080p_ass_with_bom(tmp_path: Path):
    proj = _make_valid_kbp_project()
    out_ass = tmp_path / "output.ass"

    result = convert_project_to_ass(proj, out_ass)
    assert result == out_ass
    assert out_ass.is_file()

    raw_bytes = out_ass.read_bytes()
    # UTF-8 BOM (utf_8_sig) for Aegisub / VSFilter / libass compatibility
    assert raw_bytes.startswith(b"\xef\xbb\xbf")

    text = raw_bytes.decode("utf_8_sig")
    assert "[Script Info]" in text
    assert "PlayResX: 1920" in text
    assert "PlayResY: 1080" in text
    assert "[Events]" in text
    assert "Dialogue:" in text
    assert "Hel" in text
    assert "world" in text


def test_convert_project_to_ass_respects_custom_options(tmp_path: Path):
    proj = _make_valid_kbp_project()
    out_ass = tmp_path / "custom_720p.ass"
    opts = AssExportOptions(
        target_x=1280,
        target_y=720,
        fade_in=150,
        fade_out=200,
        overflow=OVERFLOW_EVEN_SPLIT,
    )

    convert_project_to_ass(proj, out_ass, options=opts)
    text = out_ass.read_text(encoding="utf_8_sig")
    assert "PlayResX: 1280" in text
    assert "PlayResY: 720" in text
    assert r"\fad(150,200)" in text


def test_convert_kbp_file_to_ass_handles_cp1252_fallback(tmp_path: Path):
    proj = _make_valid_kbp_project("Café", "Naïve")
    kbp_path = tmp_path / "latin.kbp"
    kbp_path.write_bytes(proj.to_bytes().decode("utf-8").encode("cp1252"))

    out_ass = tmp_path / "latin.ass"
    convert_kbp_file_to_ass(
        kbp_path,
        out_ass,
        options=AssExportOptions(overflow=OVERFLOW_NO_WRAP),
    )

    text = out_ass.read_text(encoding="utf_8_sig")
    assert "[Events]" in text
    assert "Dialogue:" in text


def test_finishing_view_auto_saves_beside_kbp_without_prompt(tmp_path: Path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog

    from kbp_editor.ui.finishing_view import FinishingView

    app = QApplication.instance() or QApplication([])
    assert app is not None

    # Fail if any save dialog is opened when source_path is present
    def _unexpected_dialog(*args, **kwargs):
        raise AssertionError("QFileDialog should not be prompted when project.source_path is set")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", _unexpected_dialog)

    kbp_path = tmp_path / "My Band - My Song.kbp"
    proj = _make_valid_kbp_project("My Band", "My Song")
    proj.save(kbp_path)

    view = FinishingView()
    emitted: list[tuple[str, int]] = []
    view.status_message.connect(lambda msg, ms: emitted.append((msg, ms)))
    view.set_project(proj)

    view._convert_btn.click()

    expected_ass = tmp_path / "My Band - My Song.ass"
    assert expected_ass.is_file()
    assert not view._status_lbl.isHidden()
    assert "My Band - My Song.ass" in view._status_lbl.text()
    assert emitted and "My Band - My Song.ass" in emitted[-1][0]
