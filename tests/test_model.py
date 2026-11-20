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
