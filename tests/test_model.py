from __future__ import annotations

from pathlib import Path
import pytest

from kbp_editor.history import HistoryManager
from kbp_editor.model import KbpProject, Line, Page, Syllable, KbpHeader


REAL_KBP = Path(
    r"C:\Users\Mattm\Desktop\Karaoke\Songs\xIn Progress\8) Angels and Airwaves - True Love\Karaoke Builder Files\Angels and Airwaves - True Love.kbp"
)


def _sample_project() -> KbpProject:
    header = KbpHeader.parse([
        "-----------------------------",
        "KARAOKE BUILDER STUDIO",
        "HEADERV2",
        "'Palette Colours (0-15)",
        "  055,FFF,000,E70,940",
        "  Style00,Default,1,2,3,4",
        "    Arial,12,B,0",
        "    2,2,2,2,0,0,0,L",
        "  StyleEnd",
        "'Margins : L,R,T,Line Spacing",
        "  2,2,7,12",
    ])
    p1 = Page(lines=[
        Line("C", "A", 975, 1350, syllables=[
            Syllable("➣➣➣", 1000, 1135),
            Syllable("Hel", 1136, 1200),
            Syllable("lo", 1201, 1300),
        ]),
        Line("C", "A", 975, 1650, syllables=[
            Syllable("➣➣➣", 1360, 1495),
            Syllable("world", 1496, 1600),
        ]),
    ])
    # Page 2 immediately follows Page 1 (no instrumental gap) even though it has ➣➣➣
    p2 = Page(lines=[
        Line("C", "A", 1400, 1950, syllables=[
            Syllable("➣➣➣", 1700, 1835),
            Syllable("fast", 1836, 1900),
        ]),
    ])
    # Page 3 comes after a long instrumental gap (1900 -> 3000 = 1100 cs >= 300)
    p3 = Page(lines=[
        Line("C", "A", 2700, 3350, syllables=[
            Syllable("➣➣➣", 3000, 3135),
            Syllable("again", 3136, 3300),
        ]),
    ])
    return KbpProject(header=header, pages=[p1, p2, p3])


def test_unlimited_undo_redo_and_dirty_tracking():
    proj = _sample_project()
    hist = HistoryManager(proj)
    assert not hist.can_undo
    assert not hist.can_redo
    assert not hist.is_dirty

    for step in range(1, 26):
        proj.shift_all(10)
        hist.push(f"Shift #{step}", proj)

    assert hist.count == 26
    assert hist.is_dirty
    assert proj.pages[0].lines[0].syllables[0].start == 1000 + 250

    for _ in range(25):
        proj = hist.undo()
    assert proj is not None
    assert proj.pages[0].lines[0].syllables[0].start == 1000
    assert not hist.can_undo
    assert not hist.is_dirty
    assert hist.can_redo

    proj = hist.redo()
    assert proj is not None
    assert proj.pages[0].lines[0].syllables[0].start == 1010


def test_reset_and_optimize_windows_rules():
    proj = _sample_project()

    # Reset to default: 300 cs lead-in, 50 cs clear hold
    proj.reset_windows()
    assert proj.pages[0].lines[0].display_start == 700
    assert proj.pages[0].lines[0].display_end == 1350
    assert proj.pages[2].lines[0].display_start == 2700

    # Optimize windows: shrinks Page 0 (first page) and Page 2 (gap 1100 >= 300),
    # leaves Page 1 (gap 100 < 300) at default lead-in clamped by Page 0 row clear (1650 + 1 = 1651),
    # and never shrinks mid-page ➣➣➣
    opt_indices = proj.optimize_windows(optimized_lead_in_cs=25, gap_threshold_cs=300)
    assert opt_indices == [0, 2]
    assert proj.pages[0].lines[0].display_start == 975
    assert proj.pages[0].lines[1].display_start == 975  # mid-page ➣➣➣ stays aligned with page
    assert proj.pages[1].lines[0].display_start == 1651  # not shrunk; clamped after Page 0 Line 2 clears
    assert proj.pages[2].lines[0].display_start == 2975  # 3000 - 25 (instrumental gap)


def test_syllable_split_join_delete():
    proj = _sample_project()
    line = proj.pages[0].lines[0]
    assert len(line.syllables) == 3
    assert line.split_syllable(1, at_cs=1168)
    assert len(line.syllables) == 4
    assert line.syllables[1].end == 1168
    assert line.syllables[2].start == 1168
    assert line.join_syllables(1)
    assert len(line.syllables) == 3
    assert line.delete_syllable(0)
    assert len(line.syllables) == 2


def test_snap_to_adjacent_lyrics_and_split_join_lines():
    proj = _sample_project()
    # Add a gap around syllable 1 ("Hel": 1136..1200) -> shrink it to 1150..1185
    syl = proj.pages[0].lines[0].syllables[1]
    syl.start, syl.end = 1150, 1185
    assert proj.snap_syllable_start(0, 0, 1)
    assert syl.start == 1135  # previous syllable's end
    assert proj.snap_syllable_end(0, 0, 1)
    assert syl.end == 1201    # next syllable's start

    # Split Line 0 at syllable 1 -> now Page 0 has 3 lines
    assert proj.split_line(0, 0, 1)
    assert len(proj.pages[0].lines) == 3
    assert len(proj.pages[0].lines[0].syllables) == 1
    assert len(proj.pages[0].lines[1].syllables) == 2

    # Join Line 0 and Line 1 back together -> back to 2 lines
    assert proj.join_lines(0, 0)
    assert len(proj.pages[0].lines) == 2
    assert len(proj.pages[0].lines[0].syllables) == 3
    proj.reset_windows()
    assert proj.pages[0].lines[0].display_start <= proj.pages[0].lines[0].first_syl_start


@pytest.mark.skipif(not REAL_KBP.exists(), reason="Real KBP test file not present")
def test_real_true_love_kbp_roundtrip_and_optimize(tmp_path):
    orig_bytes = REAL_KBP.read_bytes()
    proj = KbpProject.load(REAL_KBP)
    assert len(proj.pages) == 9
    assert proj.header.title == "True Love"
    assert "A" in proj.header.styles and "B" in proj.header.styles

    # Round-trip save must match original bytes verbatim (CRLF + padding)
    out = tmp_path / "roundtrip.kbp"
    proj.save(out)
    assert out.read_bytes() == orig_bytes

    # Verify Optimized Windows detects the exact 4 instrumental-gap ➣➣➣ pages (Pages 1, 2, 4, 6 -> indices 0, 1, 3, 5)
    opt_pages = proj.optimize_windows(optimized_lead_in_cs=25, gap_threshold_cs=300)
    assert opt_pages == [0, 1, 3, 5]
    # Page 7 (index 6) starts with ➣➣➣ right after Page 6 (no gap) so it must NOT be shrunk
    assert proj.pages[6].lines[0].display_start == proj.pages[6].first_syl_start - 300


def test_insert_and_multi_delete_syllables():
    proj = _sample_project()
    line = proj.pages[0].lines[0]
    assert len(line.syllables) == 3

    # Insert two syllables ("new ", "word") at end of line
    assert line.insert_syllables(len(line.syllables), ["new ", "word"], duration_cs=25)
    assert len(line.syllables) == 5
    assert line.syllables[2].text.endswith(" ")
    assert line.syllables[3].text == "new "
    assert line.syllables[3].start == 1300
    assert line.syllables[4].text == "word"
    assert line.syllables[4].end == 1350

    # Delete multiple syllables at once
    assert line.delete_syllables([3, 4])
    assert len(line.syllables) == 3


def test_duplicate_line_and_page_assigns_b_and_c_styles():
    proj = _sample_project()
    # Duplicating an [A] line produces a [B] line right after it
    dup_l = proj.duplicate_line(0, 0)
    assert dup_l is not None
    assert dup_l.style == "B"
    assert len(proj.pages[0].lines) == 3

    # Duplicating that [B] line produces a [C] line
    dup_l2 = proj.duplicate_line(0, 1)
    assert dup_l2 is not None
    assert dup_l2.style == "C"

    # Duplicating that [C] line advances to [D], [E], [F], then wraps to [A]
    dup_l3 = proj.duplicate_line(0, 2)
    assert dup_l3 is not None and dup_l3.style == "D"
    dup_l4 = proj.duplicate_line(0, 3)
    assert dup_l4 is not None and dup_l4.style == "E"
    dup_l5 = proj.duplicate_line(0, 4)
    assert dup_l5 is not None and dup_l5.style == "F"
    dup_l6 = proj.duplicate_line(0, 5)
    assert dup_l6 is not None and dup_l6.style == "A"

    # Duplicating Page 2 ([A]) with pick_duplicate_style produces [B]
    style_b = proj.pick_duplicate_style(proj.pages[1].lines)
    assert style_b == "B"
    new_p = proj.duplicate_page(page_number=2, target_style=style_b)
    assert all(ln.style == "B" for ln in new_p.lines)

    # Duplicating the new [B] page picks [C], and a [C] page picks [D]
    style_c = proj.pick_duplicate_style(new_p.lines)
    assert style_c == "C"
    new_p_c = proj.duplicate_page(page_number=3, target_style=style_c)
    assert proj.pick_duplicate_style(new_p_c.lines) == "D"

    # In-place page duplication keeps exact same timestamps for dragging
    src_start = proj.pages[0].first_syl_start
    in_place_p = proj.duplicate_page(page_number=1, target_style="B", in_place=True)
    assert in_place_p.first_syl_start == src_start


def test_line_and_page_crud():
    proj = _sample_project()
    # Add & delete line
    orig_lines = len(proj.pages[0].lines)
    added = proj.add_line(0, 0, ["new ", "line"], duration_cs=25, style="A")
    assert added is not None
    assert len(proj.pages[0].lines) == orig_lines + 1
    assert proj.delete_line(0, 1)
    assert len(proj.pages[0].lines) == orig_lines

    # Split & join page
    orig_pages = len(proj.pages)
    assert proj.split_page(0, 1)
    assert len(proj.pages) == orig_pages + 1
    assert len(proj.pages[0].lines) == 1
    assert len(proj.pages[1].lines) == 1
    assert proj.join_pages(0)
    assert len(proj.pages) == orig_pages
    assert len(proj.pages[0].lines) == 2

    # Add & delete page
    proj.add_page(0, [["fresh ", "page"]], duration_cs=30)
    assert len(proj.pages) == orig_pages + 1
    assert proj.delete_page(1)
    assert len(proj.pages) == orig_pages


def test_non_conforming_kbp_auto_repair_and_backup(tmp_path):
    broken_kbp = tmp_path / "broken.kbp"
    raw_content = "\r\n".join([
        "-----------------------------",
        "KARAOKE BUILDER STUDIO",
        "HEADERV2",
        "'Palette Colours (0-15)",
        "  055,FFF,000,E70,940",
        "  Style00,Default,1,2,3,4",
        "    Arial,12,B,0",
        "    2,2,2,2,0,0,0,L",
        "  StyleEnd",
        "-----------------------------",
        "PAGEV2",
        "C/A/-50/1200/0/0/0",
        "Neg/           -25/100/0",
        "Swap/          500/420/0",
        "ZeroEnd/       600/0/0",
        "BadNum/        abc/def/0",
        "",
        "C/A/1500/900/0/0/0",
        "InvWin/        1000/1100/0",
        "",
        "-----------------------------",
        "",
    ])
    broken_kbp.write_text(raw_content, encoding="utf-8")
    orig_bytes = broken_kbp.read_bytes()

    proj = KbpProject.load(broken_kbp)
    # Original file on disk must be untouched!
    assert broken_kbp.read_bytes() == orig_bytes
    assert len(proj.load_issues) >= 5

    l0 = proj.pages[0].lines[0]
    assert l0.display_start == 0  # clamped from -50
    assert l0.syllables[0].start == 0 and l0.syllables[0].end == 100  # clamped from -25
    assert l0.syllables[1].start == 420 and l0.syllables[1].end == 500  # swapped
    assert l0.syllables[2].start == 600 and l0.syllables[2].end > 600  # zero end fixed
    assert l0.syllables[3].start >= 0 and l0.syllables[3].end > l0.syllables[3].start  # malformed recovered

    l1 = proj.pages[0].lines[1]
    assert l1.display_end >= l1.display_start  # inverted display window fixed

    # Non-destructive backup creation (and collision safety)
    bak1 = KbpProject.create_backup(broken_kbp)
    assert bak1.name == "broken.kbp.bak"
    assert bak1.read_bytes() == orig_bytes
    bak2 = KbpProject.create_backup(broken_kbp)
    assert bak2.name == "broken.kbp.bak1"
    assert bak2.read_bytes() == orig_bytes


def test_prepend_glyphs_matches_mjkcs():
    proj = _sample_project()
    line = proj.pages[0].lines[0]
    # Syllable 1 is "Hel" starting at 1136
    assert line.prepend_glyph(1, "➣➣➣", 135)
    g1 = line.syllables[1]
    assert g1.text == "➣➣➣"
    assert g1.end == 1135
    assert g1.start == 1000  # 1135 - 135

    # Now prepend •• (68 cs) before syllable 0 (start 1000) -> 931..999, pulls display_start if needed
    line.display_start = 980
    assert line.prepend_glyph(0, "••", 68)
    g2 = line.syllables[0]
    assert g2.text == "••"
    assert g2.end == 999
    assert g2.start == 931
    assert line.display_start == 931


def test_insert_syllable_between_adjacent_and_short_neighbors():
    # Case 1: long prev (32 cs) + short next (16 cs) adjacent -> borrows proportionally, never squishes < 8 cs, no overlap
    ln = Line(
        "C", "A", 17600, 17900,
        syllables=[
            Syllable("light ", 17697, 17729),  # 32 cs
            Syllable("is ", 17729, 17745),     # 16 cs
            Syllable("the ", 17745, 17761),    # 16 cs
        ],
    )
    assert ln.insert_syllables(1, ["bright"], duration_cs=30)
    assert len(ln.syllables) == 4
    # Strictly non-overlapping and every syllable >= 8 cs
    for i, s in enumerate(ln.syllables):
        assert s.duration >= 8, f"Syllable {s.text} squished to {s.duration} cs"
        if i > 0:
            assert s.start >= ln.syllables[i - 1].end, "Inserted syllable overlapped neighbor!"
    # Longer neighbor ('light') absorbed more of the trim than short neighbor ('is')
    assert (17729 - ln.syllables[0].end) > (ln.syllables[2].start - 17729)

    # Case 2: two ultra-short 8 cs neighbors with a downstream gap -> ripples minimally and absorbs into gap
    ln2 = Line(
        "C", "A", 900, 1200,
        syllables=[
            Syllable("a ", 1000, 1008),    # 8 cs (at minimum)
            Syllable("b ", 1008, 1016),    # 8 cs (at minimum)
            Syllable("c", 1040, 1080),     # 24 cs gap before 'c'
        ],
    )
    assert ln2.insert_syllables(1, ["x"], duration_cs=30)
    assert len(ln2.syllables) == 4
    for i, s in enumerate(ln2.syllables):
        assert s.duration >= 8
        if i > 0:
            assert s.start >= ln2.syllables[i - 1].end
    # Downstream 'c' was completely untouched because the ripple was absorbed by the gap!
    assert ln2.syllables[3].start == 1040 and ln2.syllables[3].end == 1080


def test_nudge_syllable_border_sticky_and_non_sticky():
    # Syllables: "Hel" (100..150), "lo " (151..200) [touching within 2 cs], "world" (230..280) [gap 30 cs]
    ln = Line(
        "C", "A", 50, 330,
        syllables=[
            Syllable("Hel", 100, 150),
            Syllable("lo ", 151, 200),
            Syllable("world", 230, 280),
        ],
    )

    # 1. Sticky ON: nudging "lo " left border left by -5 cs pulls touching "Hel" end along with it
    assert ln.nudge_syllable_border(1, "left", -5, sticky=True)
    assert ln.syllables[1].start == 146
    assert ln.syllables[0].end == 146

    # 2. Sticky ON: nudging "lo " left border right by +3 cs pushes "Hel" end right along with it
    assert ln.nudge_syllable_border(1, "left", +3, sticky=True)
    assert ln.syllables[1].start == 149
    assert ln.syllables[0].end == 149

    # 3. Sticky ON, but "lo " and "world" have a 30 cs gap (> 2 cs) -> NOT sticky, moves independently
    assert ln.nudge_syllable_border(1, "right", +10, sticky=True)
    assert ln.syllables[1].end == 210
    assert ln.syllables[2].start == 230

    # 4. Sticky OFF: nudging "lo " left border right leaves "Hel" end untouched
    assert ln.nudge_syllable_border(1, "left", +5, sticky=False)
    assert ln.syllables[1].start == 154
    assert ln.syllables[0].end == 149

    # 5. Clamping: cannot cross neighbor or collapse duration below 1 cs
    assert ln.nudge_syllable_border(1, "left", -100, sticky=False)
    assert ln.syllables[1].start == 149  # clamped to prev.end
    assert not ln.nudge_syllable_border(1, "left", -1, sticky=False)  # already at limit

    # 6. Sticky clamping: cannot collapse neighbor below 1 cs duration
    assert ln.nudge_syllable_border(1, "left", -100, sticky=True)
    assert ln.syllables[1].start == 101
    assert ln.syllables[0].end == 101  # prev keeps >= 1 cs duration (100..101)
    assert not ln.nudge_syllable_border(1, "left", -1, sticky=True)


def test_flush_one_cs_gaps_zero_drift():
    proj = _sample_project()
    # Record every original start timestamp across the project
    orig_starts = [
        [s.start for s in ln.syllables]
        for p in proj.pages
        for ln in p.lines
    ]
    # Add a deliberate 20 cs gap in Page 0 Line 0 between syl 1 and syl 2? Keep original first:
    # _sample_project has 1 cs gaps: 1135->1136, 1200->1201, 1495->1496, 1835->1836, 3135->3136 (5 total)
    flushed = proj.flush_one_cs_gaps()
    assert flushed == 5

    # Every start timestamp must be 100% untouched (zero drift!)
    new_starts = [
        [s.start for s in ln.syllables]
        for p in proj.pages
        for ln in p.lines
    ]
    assert new_starts == orig_starts

    # All 1 cs gaps are now 0 cs flush
    l0 = proj.pages[0].lines[0]
    assert l0.syllables[0].end == l0.syllables[1].start == 1136
    assert l0.syllables[1].end == l0.syllables[2].start == 1201

    # Idempotent: second call flushes 0
    assert proj.flush_one_cs_gaps() == 0

    # Larger gaps (>= 2 cs) are NOT touched
    l0.syllables[1].end = 1190
    assert proj.flush_one_cs_gaps() == 0
    assert l0.syllables[1].end == 1190


def test_header_set_audio_path_updates_existing_and_missing(tmp_path):
    # Case 1: Header with existing Audio line preserves prefix and updates round-trip
    hdr = KbpHeader.parse([
        "Title   My Song",
        "Artist  My Band",
        "Audio   C:\\missing\\old.mp3",
    ])
    hdr.set_audio_path(r"C:\music\fixed.flac")
    assert hdr.audio_path == r"C:\music\fixed.flac"
    assert "Audio   C:\\music\\fixed.flac" in hdr.raw_lines
    reparsed = KbpHeader.parse(hdr.raw_lines)
    assert reparsed.audio_path == r"C:\music\fixed.flac"

    # Case 2: Header without Audio line inserts one cleanly
    hdr2 = KbpHeader.parse(["Title   No Audio Yet", "Artist  Band"])
    hdr2.set_audio_path(r"C:\music\added.wav")
    assert hdr2.audio_path == r"C:\music\added.wav"
    assert KbpHeader.parse(hdr2.raw_lines).audio_path == r"C:\music\added.wav"
