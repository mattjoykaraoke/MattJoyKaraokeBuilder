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

    # Save & reload JSON draft
    jpath = tmp_path / "draft.kbsync.json"
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
