from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, List, Optional, Sequence, Tuple


SEPARATOR = "-----------------------------"
DEFAULT_LEAD_IN_CS = 300
DEFAULT_CLEAR_HOLD_CS = 50
DEFAULT_OPTIMIZED_LEAD_IN_CS = 25
DEFAULT_GAP_THRESHOLD_CS = 300
DEFAULT_LEAD_IN_MARKER = "➣➣➣"
KNOWN_GLYPHS: Tuple[str, ...] = (DEFAULT_LEAD_IN_MARKER, "••")


def is_non_alnum_glyph_text(text: str) -> bool:
    """True when `text` has visible characters and none of them are alphanumeric (e.g. ➣➣➣, ••, ▅▅▅▅▅▅▅, ➤)."""
    stripped = text.strip()
    if not stripped:
        return False
    return not any(ch.isalnum() for ch in stripped)
DEFAULT_KBP_COMMENTS: Tuple[str, ...] = (
    "Hacked by Matt Joy",
    "github.com/mattjoykaraoke",
)


def parse_line_to_syllables(raw_line: str) -> List[str]:
    """
    Parse a single KBS / Local Lyric Splitter line into KBP syllable strings.
    - Spaces separate words (trailing space attached to the last syllable of each word except the final word).
    - '/' splits a word into explicit syllables (e.g. 'ev/ery/thing' -> ['ev', 'ery', 'thing'],
      '➣➣➣/The snow' -> ['➣➣➣', 'The ', 'snow']).
    """
    words = [w for w in raw_line.strip().split(" ") if w]
    out: List[str] = []
    for w_idx, word in enumerate(words):
        parts = [p for p in word.split("/") if p]
        for p_idx, part in enumerate(parts):
            is_last_of_word = p_idx == len(parts) - 1
            is_last_word = w_idx == len(words) - 1
            out.append(part + (" " if (is_last_of_word and not is_last_word) else ""))
    return out


def read_kbp_text(path: str | Path, *, allow_bom: bool = False) -> str:
    """Read a text/KBP file using UTF-8 (or UTF-8-SIG when allow_bom=True) with CP1252 fallback."""
    p = Path(path)
    raw = p.read_bytes()
    primary_enc = "utf-8-sig" if allow_bom else "utf-8"
    try:
        return raw.decode(primary_enc)
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def extract_kbp_header_lines(
    raw_lines: Sequence[str],
    section_markers: Sequence[str] = ("PAGEV2", "LYRICSV2"),
) -> Tuple[Optional[int], List[str]]:
    """
    Locate the first section marker (e.g. PAGEV2 or LYRICSV2) and return
    (marker_index, header_lines_before_preceding_separator).
    """
    marker_set = set(section_markers)
    marker_idx = next((i for i, ln in enumerate(raw_lines) if ln.strip() in marker_set), None)
    if marker_idx is None:
        return None, []
    sep_idx = marker_idx - 1
    while sep_idx > 0 and not raw_lines[sep_idx].startswith("---"):
        sep_idx -= 1
    return marker_idx, list(raw_lines[:sep_idx])


def clean_filename_part(text: str) -> str:
    """Strip characters that are invalid in Windows/POSIX filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", text).strip()


def suggest_project_filename(
    artist: str = "",
    title: str = "",
    *,
    ext: str = ".kbp",
    audio_path: Optional[str | Path] = None,
    fallback_stem: str = "untitled",
) -> str:
    """Build a sanitized 'Artist - Title<ext>' filename with audio-stem and default fallbacks."""
    c_artist = clean_filename_part(artist)
    c_title = clean_filename_part(title)
    if c_artist and c_title:
        stem = f"{c_artist} - {c_title}"
    elif c_title:
        stem = c_title
    elif c_artist:
        stem = c_artist
    elif audio_path:
        stem = clean_filename_part(Path(audio_path).stem) or fallback_stem
    else:
        stem = fallback_stem
    if not stem:
        return ""
    suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{stem}{suffix}"


def parse_12bit_rgb(code: str) -> Tuple[int, int, int]:
    """Convert a 3-hex-digit KBS color (e.g. 'E70') to 8-bit (R, G, B)."""
    s = code.strip()
    if len(s) != 3:
        return (255, 255, 255)
    try:
        return (int(s[0], 16) * 17, int(s[1], 16) * 17, int(s[2], 16) * 17)
    except ValueError:
        return (255, 255, 255)


def style_index_to_letter(index: int) -> str:
    return chr(ord("A") + max(0, min(25, index)))


def style_letter_to_index(letter: str) -> int:
    if not letter:
        return 0
    return max(0, ord(letter[0].upper()) - ord("A"))


@dataclass
class KbpStyle:
    index: int
    name: str
    text_color_idx: int = 1
    outline_color_idx: int = 2
    wipe_color_idx: int = 3
    wipe_outline_color_idx: int = 4
    font_name: str = "Arial"
    font_size: int = 12
    font_style: str = "B"  # B, I, BI, R
    charset: int = 0
    outline_widths: Tuple[int, int, int, int] = (2, 2, 2, 2)
    shadow_offset: Tuple[int, int] = (0, 0)
    wiping: int = 0
    uppercase: str = "L"  # U = uppercase, L = normal

    @property
    def letter(self) -> str:
        return style_index_to_letter(self.index)

    @property
    def is_bold(self) -> bool:
        return "B" in self.font_style.upper()

    @property
    def is_italic(self) -> bool:
        return "I" in self.font_style.upper()


@dataclass
class KbpHeader:
    raw_lines: List[str]
    palette: List[Tuple[int, int, int]] = field(default_factory=list)
    styles: Dict[str, KbpStyle] = field(default_factory=dict)
    margins: Tuple[int, int, int, int] = (2, 2, 7, 12)  # L, R, T, Line Spacing
    border_color_idx: int = 0
    detail_level: int = 2
    title: str = ""
    artist: str = ""
    audio_path: str = ""
    status: str = "1"
    comments: List[str] = field(default_factory=list)

    def color(self, idx: int, fallback: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
        if 0 <= idx < len(self.palette):
            return self.palette[idx]
        return fallback

    def get_style(self, letter: str) -> KbpStyle:
        if letter in self.styles:
            return self.styles[letter]
        if "A" in self.styles:
            return self.styles["A"]
        return KbpStyle(index=0, name="Default")

    @classmethod
    def parse(cls, raw_lines: List[str]) -> "KbpHeader":
        header = cls(raw_lines=list(raw_lines))
        i = 0
        n = len(raw_lines)
        while i < n:
            line = raw_lines[i]
            stripped = line.strip()

            if stripped.startswith("'Palette Colours"):
                if i + 1 < n:
                    codes = [c.strip() for c in raw_lines[i + 1].strip().split(",") if c.strip()]
                    header.palette = [parse_12bit_rgb(c) for c in codes]
                    i += 2
                    continue

            if stripped.startswith("Style") and not stripped.startswith("StyleEnd") and "," in stripped:
                parts = [p.strip() for p in stripped.split(",")]
                if len(parts) >= 6 and parts[0].startswith("Style"):
                    try:
                        s_idx = int(parts[0][5:])
                        style = KbpStyle(
                            index=s_idx,
                            name=parts[1],
                            text_color_idx=int(parts[2]),
                            outline_color_idx=int(parts[3]),
                            wipe_color_idx=int(parts[4]),
                            wipe_outline_color_idx=int(parts[5]),
                        )
                        if i + 1 < n:
                            f_parts = [p.strip() for p in raw_lines[i + 1].strip().split(",")]
                            if len(f_parts) >= 4:
                                style.font_name = f_parts[0]
                                style.font_size = int(f_parts[1])
                                style.font_style = f_parts[2]
                                style.charset = int(f_parts[3])
                        if i + 2 < n:
                            o_parts = [p.strip() for p in raw_lines[i + 2].strip().split(",")]
                            if len(o_parts) >= 8:
                                style.outline_widths = (
                                    int(o_parts[0]),
                                    int(o_parts[1]),
                                    int(o_parts[2]),
                                    int(o_parts[3]),
                                )
                                style.shadow_offset = (int(o_parts[4]), int(o_parts[5]))
                                style.wiping = int(o_parts[6])
                                style.uppercase = o_parts[7]
                        header.styles[style.letter] = style
                        i += 3
                        continue
                    except ValueError:
                        pass

            if stripped.startswith("'Margins"):
                if i + 1 < n:
                    m_parts = [p.strip() for p in raw_lines[i + 1].strip().split(",")]
                    if len(m_parts) >= 4:
                        try:
                            header.margins = (
                                int(m_parts[0]),
                                int(m_parts[1]),
                                int(m_parts[2]),
                                int(m_parts[3]),
                            )
                        except ValueError:
                            pass
                    i += 2
                    continue

            if stripped.startswith("'Other:"):
                if i + 1 < n:
                    o_parts = [p.strip() for p in raw_lines[i + 1].strip().split(",")]
                    if len(o_parts) >= 2:
                        try:
                            header.border_color_idx = int(o_parts[0])
                            header.detail_level = int(o_parts[1])
                        except ValueError:
                            pass
                    i += 2
                    continue

            if line.startswith("Title"):
                header.title = line[5:].strip()
            elif line.startswith("Artist"):
                header.artist = line[6:].strip()
            elif line.startswith("Audio"):
                header.audio_path = line[5:].strip()
            elif line.startswith("Status"):
                header.status = line[6:].strip()
            elif line.startswith("Comments"):
                parsed_comments: List[str] = []
                first = line[8:].lstrip(" \t").rstrip()
                if first:
                    parsed_comments.append(first)
                j = i + 1
                while j < n:
                    cont = raw_lines[j]
                    if cont.startswith("          ") and cont.strip():
                        parsed_comments.append(cont[10:].rstrip())
                        j += 1
                    else:
                        break
                header.comments = parsed_comments
                i = j
                continue

            i += 1

        if not header.palette:
            header.palette = [
                (0, 85, 85),
                (255, 255, 255),
                (0, 0, 0),
                (238, 119, 0),
                (153, 68, 0),
            ]
        return header

    def set_field(
        self,
        field_name: str,
        value: str,
        *,
        default_pad: str = "     ",
        insert_after: Sequence[str] = (),
    ) -> None:
        """Update or insert a single-line field in `raw_lines` while preserving column padding."""
        for i, line in enumerate(self.raw_lines):
            if line.startswith(field_name):
                rest = line[len(field_name) :]
                pad_len = len(rest) - len(rest.lstrip(" \t"))
                pad = rest[:pad_len] if pad_len > 0 else default_pad
                self.raw_lines[i] = f"{field_name}{pad}{value}"
                return
        insert_at = len(self.raw_lines)
        if insert_after:
            for i, line in enumerate(self.raw_lines):
                if any(line.startswith(prefix) for prefix in insert_after):
                    insert_at = i + 1
        self.raw_lines.insert(insert_at, f"{field_name}{default_pad}{value}")

    def set_title(self, title: str) -> None:
        self.title = str(title)
        self.set_field("Title", self.title, default_pad="     ")

    def set_artist(self, artist: str) -> None:
        self.artist = str(artist)
        self.set_field("Artist", self.artist, default_pad="    ", insert_after=("Title",))

    def set_audio_path(self, path: str | Path) -> None:
        """Update the project's main audio path in both `audio_path` and `raw_lines`."""
        new_path = str(path)
        self.audio_path = new_path
        self.set_field("Audio", new_path, default_pad="   ", insert_after=("Artist", "Title"))

    def set_comments(self, lines: Sequence[str]) -> None:
        """
        Update the HEADERV2 Comments block using exact KBS fixed-column formatting:
          Comments  <line 1>
                    <line 2>
        (10-character prefix: 'Comments  ' on line 1, 10 spaces on continuation lines).
        """
        cleaned = [str(ln).rstrip("\r\n") for ln in lines if str(ln).strip()]
        self.comments = list(cleaned)
        if cleaned:
            formatted = [f"Comments  {cleaned[0]}"] + [f"          {ln}" for ln in cleaned[1:]]
        else:
            formatted = ["Comments  "]

        for i, line in enumerate(self.raw_lines):
            if line.startswith("Comments"):
                end = i + 1
                while end < len(self.raw_lines):
                    cont = self.raw_lines[end]
                    if cont.startswith("          ") and cont.strip():
                        end += 1
                    else:
                        break
                self.raw_lines[i:end] = formatted
                return

        # No existing Comments field — append cleanly with blank separator lines
        if self.raw_lines and self.raw_lines[-1].strip():
            self.raw_lines.append("")
        self.raw_lines.extend(formatted)
        self.raw_lines.append("")

    def ensure_default_comments(self, default_lines: Sequence[str] = DEFAULT_KBP_COMMENTS) -> None:
        """Populate the default KBS Comments block if no comments are currently set."""
        if not self.comments:
            self.set_comments(default_lines)


@dataclass
class Syllable:
    text: str          # e.g. "(True " or "love)" — trailing space marks end of word
    start: int         # centiseconds (1/100 s)
    end: int           # centiseconds
    flag: str = "0"    # KBS syllable flag (usually "0")
    pad: int = 0       # spaces after "/" to preserve original column alignment
    is_glyph: bool = False  # True for appended lead-in glyphs (preserved if glyph text is edited)

    def __post_init__(self) -> None:
        if not self.is_glyph:
            stripped = self.text.strip()
            if stripped in KNOWN_GLYPHS or stripped.startswith(DEFAULT_LEAD_IN_MARKER):
                self.is_glyph = True

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)

    def shift(self, delta_cs: int) -> None:
        self.start = max(0, self.start + delta_cs)
        self.end = max(self.start, self.end + delta_cs)

    def format(self) -> str:
        prefix = f"{self.text}/"
        spaces = self.pad if self.pad > 0 else max(1, 15 - len(prefix))
        return f"{prefix}{' ' * spaces}{self.start}/{self.end}/{self.flag}"


@dataclass
class Line:
    align: str                 # "C" (Center), "L", "R"
    style: str                 # "A" (Style00), "B" (Style01), ...
    display_start: int         # centiseconds when line appears on screen
    display_end: int           # centiseconds when line clears from screen
    extra_fields: List[str] = field(default_factory=lambda: ["0", "0", "0"])
    syllables: List[Syllable] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.refresh_leading_glyph()

    @property
    def is_spacer(self) -> bool:
        """True for blank layout-spacer lines (no syllables, or only empty 0/0 placeholder syllables)."""
        if not self.syllables:
            return True
        return all(not s.text.strip() and s.start == 0 and s.end == 0 for s in self.syllables)

    @property
    def is_fixed(self) -> bool:
        """True when the line uses KBP fixed-text mode (lowercase style letter, no wiping)."""
        return bool(self.style) and self.style.islower()

    def set_fixed(self, fixed: bool) -> None:
        """Toggle KBP fixed-text mode by lowercasing/uppercasing the style letter."""
        if not self.style:
            self.style = "a" if fixed else "A"
        else:
            self.style = self.style.lower() if fixed else self.style.upper()

    @property
    def offset_x(self) -> int:
        """KBP horizontal pixel offset (`right`, extra_fields[0]) in CD+G units."""
        if len(self.extra_fields) >= 1:
            try:
                return int(self.extra_fields[0])
            except ValueError:
                return 0
        return 0

    @property
    def offset_y(self) -> int:
        """KBP vertical pixel offset (`down`, extra_fields[1]) in CD+G units."""
        if len(self.extra_fields) >= 2:
            try:
                return int(self.extra_fields[1])
            except ValueError:
                return 0
        return 0

    def set_offset_y(self, val: int) -> None:
        """Set KBP vertical pixel offset (`down`, extra_fields[1]) in CD+G units."""
        while len(self.extra_fields) < 3:
            self.extra_fields.append("0")
        self.extra_fields[1] = str(int(val))

    def refresh_leading_glyph(self) -> None:
        """Mark a leading non-alphanumeric syllable on a timed line as a lead-in glyph."""
        if self.is_spacer or self.is_fixed or not self.syllables:
            return
        first = self.syllables[0]
        if is_non_alnum_glyph_text(first.text):
            first.is_glyph = True

    @property
    def first_syl_start(self) -> int:
        return min((s.start for s in self.syllables), default=self.display_start)

    @property
    def last_syl_end(self) -> int:
        return max((s.end for s in self.syllables), default=self.display_end)

    @property
    def full_text(self) -> str:
        return "".join(s.text for s in self.syllables)

    def shift(self, delta_cs: int) -> None:
        self.display_start = max(0, self.display_start + delta_cs)
        self.display_end = max(self.display_start, self.display_end + delta_cs)
        for s in self.syllables:
            s.shift(delta_cs)

    def split_syllable(
        self,
        syl_idx: int,
        at_cs: Optional[int] = None,
        left_text: Optional[str] = None,
        right_text: Optional[str] = None,
    ) -> bool:
        """Split syllable at syl_idx into two consecutive syllables with optional custom text & time."""
        if not (0 <= syl_idx < len(self.syllables)):
            return False
        syl = self.syllables[syl_idx]
        if syl.end - syl.start < 2:
            return False
        mid_cs = at_cs if at_cs is not None else (syl.start + syl.end) // 2
        mid_cs = max(syl.start + 1, min(syl.end - 1, mid_cs))

        if left_text is None or right_text is None:
            raw_text = syl.text
            stripped = raw_text.rstrip(" ")
            trailing_spaces = raw_text[len(stripped):]
            if len(stripped) >= 2:
                cut = len(stripped) // 2
                left_text = stripped[:cut]
                right_text = stripped[cut:] + trailing_spaces
            else:
                left_text = stripped
                right_text = trailing_spaces or stripped

        left = Syllable(text=left_text, start=syl.start, end=mid_cs, flag=syl.flag, is_glyph=syl.is_glyph)
        right = Syllable(text=right_text, start=mid_cs, end=syl.end, flag=syl.flag, is_glyph=syl.is_glyph)
        self.syllables[syl_idx : syl_idx + 1] = [left, right]
        return True

    def join_syllables(self, syl_idx: int) -> bool:
        """Merge syllable at syl_idx with syl_idx + 1."""
        if not (0 <= syl_idx < len(self.syllables) - 1):
            return False
        a = self.syllables[syl_idx]
        b = self.syllables[syl_idx + 1]
        merged = Syllable(
            text=a.text + b.text,
            start=min(a.start, b.start),
            end=max(a.end, b.end),
            flag=a.flag,
            is_glyph=(a.is_glyph and b.is_glyph),
        )
        self.syllables[syl_idx : syl_idx + 2] = [merged]
        return True

    def delete_syllable(self, syl_idx: int) -> bool:
        """Delete syllable at syl_idx (keeps at least 1 syllable per line)."""
        if len(self.syllables) <= 1 or not (0 <= syl_idx < len(self.syllables)):
            return False
        del self.syllables[syl_idx]
        return True

    def delete_syllables(self, indices: Sequence[int]) -> bool:
        """Delete multiple syllables by index (keeps at least 1 syllable in the line)."""
        valid = sorted({i for i in indices if 0 <= i < len(self.syllables)}, reverse=True)
        if not valid:
            return False
        # Keep at least 1 syllable
        if len(valid) >= len(self.syllables):
            valid = valid[:-1]
        if not valid:
            return False
        for i in valid:
            del self.syllables[i]
        return True

    def insert_syllables(
        self,
        insert_idx: int,
        texts: Sequence[str],
        start_cs: Optional[int] = None,
        duration_cs: int = 30,
    ) -> bool:
        """
        Insert one or more syllables into a synced line at `insert_idx`, computing
        clean non-overlapping timestamps and preserving word spacing.

        When inserting between two synced syllables without enough gap:
        1. Uses any existing gap first (0 disruption).
        2. Borrows spare time proportionally from the preceding and following syllables
           while never squishing any syllable below MIN_SYL_CS (8 cs).
        3. If both neighbors are already too short, ripples the minimal deficit rightward
           and absorbs it into the first downstream gap.
        """
        MIN_SYL_CS = 8
        cleaned = [t for t in texts if t]
        if not cleaned:
            return False
        insert_idx = max(0, min(len(self.syllables), insert_idx))
        count = len(cleaned)
        dur = max(MIN_SYL_CS, int(duration_cs))

        # Ensure trailing word space on the preceding syllable when adding a new word
        if insert_idx > 0 and self.syllables:
            prev_t = self.syllables[insert_idx - 1].text
            if prev_t and not prev_t.endswith(" ") and not cleaned[0].startswith(" "):
                self.syllables[insert_idx - 1].text = prev_t + " "

        # If inserting before an existing syllable, ensure the last inserted chunk ends with space
        if insert_idx < len(self.syllables) and not cleaned[-1].endswith(" "):
            cleaned[-1] = cleaned[-1] + " "

        # Determine start & per-syllable step
        if start_cs is not None:
            t0 = max(0, int(start_cs))
            step = dur
        elif not self.syllables:
            t0 = max(0, self.display_start)
            step = dur
        elif insert_idx == 0:
            nxt_start = self.syllables[0].start
            t0 = max(0, nxt_start - dur * count)
            avail = max(count, nxt_start - t0)
            step = max(MIN_SYL_CS, min(dur, avail // count)) if nxt_start > t0 else dur
        elif insert_idx >= len(self.syllables):
            t0 = self.syllables[-1].end
            step = dur
        else:
            prev_syl = self.syllables[insert_idx - 1]
            nxt_syl = self.syllables[insert_idx]
            gap = max(0, nxt_syl.start - prev_syl.end)
            min_needed = MIN_SYL_CS * count
            # Target a modest, grabbable slot when squeezing between synced neighbors
            target_total = min(dur * count, max(min_needed, 14 * count))

            if gap >= min_needed:
                # Stage 1: fits cleanly inside existing gap
                t0 = prev_syl.end
                step = min(dur, gap // count)
            else:
                # Stage 2: borrow proportionally from prev & next above MIN_SYL_CS
                prev_spare = max(0, prev_syl.duration - MIN_SYL_CS)
                nxt_spare = max(0, nxt_syl.duration - MIN_SYL_CS)
                total_spare = prev_spare + nxt_spare
                deficit = target_total - gap

                if total_spare > 0:
                    borrow = min(deficit, total_spare)
                    take_prev = int(round(borrow * (prev_spare / total_spare)))
                    take_prev = min(prev_spare, take_prev)
                    take_nxt = min(nxt_spare, borrow - take_prev)
                    # If rounding left remainder and prev still has spare, take it
                    rem_b = borrow - take_prev - take_nxt
                    if rem_b > 0 and prev_spare > take_prev:
                        extra = min(rem_b, prev_spare - take_prev)
                        take_prev += extra
                    prev_syl.end -= take_prev
                    nxt_syl.start += take_nxt

                avail = max(0, nxt_syl.start - prev_syl.end)
                # Stage 3: if both neighbors were too short, ripple minimal deficit right
                if avail < min_needed:
                    shift_rem = min_needed - avail
                    for j in range(insert_idx, len(self.syllables)):
                        cur_s = self.syllables[j]
                        next_gap = (
                            max(0, self.syllables[j + 1].start - cur_s.end)
                            if j + 1 < len(self.syllables)
                            else 0
                        )
                        cur_s.start += shift_rem
                        cur_s.end += shift_rem
                        # Absorb ripple into the next gap so later syllables stay untouched
                        shift_rem = max(0, shift_rem - next_gap)
                        if shift_rem == 0:
                            break
                    avail = max(min_needed, nxt_syl.start - prev_syl.end)

                t0 = prev_syl.end
                step = max(MIN_SYL_CS, avail // count)

        new_syls: List[Syllable] = []
        cur = t0
        for i, txt in enumerate(cleaned):
            # Ensure last inserted syllable fits cleanly up to next_syl.start when between neighbors
            if start_cs is None and 0 < insert_idx < len(self.syllables) and i == count - 1:
                nxt_s = self.syllables[insert_idx].start
                end_cs = min(cur + step, nxt_s) if nxt_s > cur else cur + step
            else:
                end_cs = cur + step
            new_syls.append(Syllable(text=txt, start=cur, end=end_cs))
            cur = end_cs

        self.syllables[insert_idx:insert_idx] = new_syls
        if new_syls[0].start < self.display_start:
            self.display_start = new_syls[0].start
        if new_syls[-1].end > self.display_end:
            self.display_end = new_syls[-1].end + DEFAULT_CLEAR_HOLD_CS
        return True

    def prepend_glyph(self, syl_idx: int, glyph_str: str, duration_cs: int) -> bool:
        """
        Insert a lead-in glyph syllable (e.g. '➣➣➣' = 135 cs, '••' = 68 cs) immediately
        before `syl_idx`, matching Matt Joy Karaoke Creation Suite's exact timing rules.
        """
        if not (0 <= syl_idx < len(self.syllables)) or not glyph_str:
            return False
        word = self.syllables[syl_idx]
        glyph_end = max(0, word.start - 1)
        glyph_start = max(0, glyph_end - max(1, int(duration_cs)))
        glyph_syl = Syllable(
            text=glyph_str,
            start=glyph_start,
            end=glyph_end,
            flag=word.flag,
            pad=word.pad,
            is_glyph=True,
        )
        self.syllables.insert(syl_idx, glyph_syl)
        if glyph_start < self.display_start:
            self.display_start = glyph_start
        return True

    def nudge_syllable_border(
        self,
        syl_idx: int,
        edge: str,
        delta_cs: int,
        sticky: bool = True,
        sticky_tolerance_cs: int = 2,
    ) -> bool:
        """
        Nudge a syllable's 'left' (start) or 'right' (end) border by `delta_cs`.
        When `sticky` is True and the adjacent syllable is touching (gap <= sticky_tolerance_cs),
        the shared border moves together while preserving at least 1 cs duration for both syllables.
        Returns True if any timestamp changed.
        """
        if not (0 <= syl_idx < len(self.syllables)) or delta_cs == 0:
            return False
        syl = self.syllables[syl_idx]

        if edge == "left":
            sticky_neighbor: Optional[Syllable] = None
            if sticky and syl_idx > 0:
                prev_syl = self.syllables[syl_idx - 1]
                if abs(prev_syl.end - syl.start) <= sticky_tolerance_cs:
                    sticky_neighbor = prev_syl

            if sticky_neighbor is not None:
                min_cs = sticky_neighbor.start + 1
            elif syl_idx > 0:
                min_cs = self.syllables[syl_idx - 1].end
            else:
                min_cs = 0

            max_cs = syl.end - 1
            if max_cs < min_cs:
                return False
            new_start = max(min_cs, min(syl.start + delta_cs, max_cs))
            if new_start == syl.start:
                return False
            syl.start = new_start
            if sticky_neighbor is not None:
                sticky_neighbor.end = new_start
            return True

        if edge == "right":
            sticky_neighbor = None
            if sticky and syl_idx + 1 < len(self.syllables):
                next_syl = self.syllables[syl_idx + 1]
                if abs(next_syl.start - syl.end) <= sticky_tolerance_cs:
                    sticky_neighbor = next_syl

            if sticky_neighbor is not None:
                max_cs = sticky_neighbor.end - 1
            elif syl_idx + 1 < len(self.syllables):
                max_cs = self.syllables[syl_idx + 1].start
            else:
                max_cs = 10_000_000

            min_cs = syl.start + 1
            if max_cs < min_cs:
                return False
            new_end = min(max_cs, max(syl.end + delta_cs, min_cs))
            if new_end == syl.end:
                return False
            syl.end = new_end
            if sticky_neighbor is not None:
                sticky_neighbor.start = new_end
            return True

        return False

    def format_lines(self) -> List[str]:
        header = "/".join(
            [self.align, self.style, str(self.display_start), str(self.display_end), *self.extra_fields]
        )
        return [header, *[s.format() for s in self.syllables], ""]


@dataclass
class Page:
    lines: List[Line] = field(default_factory=list)

    @property
    def non_spacer_lines(self) -> List[Line]:
        """All lines excluding blank 0/0 layout spacers."""
        return [line for line in self.lines if not line.is_spacer]

    @property
    def timed_lines(self) -> List[Line]:
        """Lines with real singing/wipe timing (excludes blank spacers and fixed-text labels)."""
        return [line for line in self.lines if not line.is_spacer and not line.is_fixed]

    @property
    def first_timed_line_index(self) -> Optional[int]:
        """0-based index of the first non-spacer, non-fixed line on this page, or None."""
        for idx, line in enumerate(self.lines):
            if not line.is_spacer and not line.is_fixed:
                return idx
        return None

    @property
    def first_syl_start(self) -> int:
        candidates = self.timed_lines or self.non_spacer_lines
        starts = [line.first_syl_start for line in candidates if line.syllables]
        return min(starts) if starts else 0

    @property
    def last_syl_end(self) -> int:
        candidates = self.timed_lines or self.non_spacer_lines
        ends = [line.last_syl_end for line in candidates if line.syllables]
        return max(ends) if ends else 0

    @property
    def display_start(self) -> int:
        candidates = self.non_spacer_lines
        return min((line.display_start for line in candidates), default=0)

    @property
    def display_end(self) -> int:
        candidates = self.non_spacer_lines
        return max((line.display_end for line in candidates), default=0)

    def starts_with_marker(self, marker: str = DEFAULT_LEAD_IN_MARKER) -> bool:
        """True if the first timed line's leading syllable is a lead-in glyph or begins with `marker`."""
        candidates = self.timed_lines
        if not candidates or not candidates[0].syllables:
            return False
        first_syl = candidates[0].syllables[0]
        if first_syl.is_glyph:
            return True
        first_text = first_syl.text.strip()
        return first_text.startswith(marker) or is_non_alnum_glyph_text(first_text)

    def shift(self, delta_cs: int) -> None:
        for line in self.lines:
            line.shift(delta_cs)

    def format_lines(self) -> List[str]:
        out = [SEPARATOR, "PAGEV2"]
        for line in self.lines:
            out.extend(line.format_lines())
        return out


@dataclass
class KbpIssue:
    """Describes a non-conforming KBP entry that was detected and auto-repaired on load."""

    location: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.location}: {self.detail}"


@dataclass
class KbpProject:
    header: KbpHeader
    pages: List[Page]
    source_path: Optional[Path] = None
    trailing_blank_lines: int = 0
    duet_mode: bool = False
    load_issues: List[KbpIssue] = field(default_factory=list)

    @property
    def header_lines(self) -> List[str]:
        return self.header.raw_lines

    @property
    def total_duration_cs(self) -> int:
        if not self.pages:
            return 0
        return max(
            max(p.display_end, p.last_syl_end)
            for p in self.pages
        )

    @classmethod
    def load(cls, path: str | Path, auto_repair: bool = True) -> "KbpProject":
        p = Path(path)
        text = read_kbp_text(p)

        raw_lines = text.splitlines()
        page_indices = [i for i, l in enumerate(raw_lines) if l.strip() == "PAGEV2"]
        if not page_indices:
            raise ValueError("No PAGEV2 sections found (file may still be unsynced LYRICSV2).")

        _, header_lines = extract_kbp_header_lines(raw_lines, section_markers=("PAGEV2",))
        header = KbpHeader.parse(header_lines)

        issues: List[KbpIssue] = []
        pages: List[Page] = []
        for idx, p_start in enumerate(page_indices):
            p_end = page_indices[idx + 1] - 1 if idx + 1 < len(page_indices) else len(raw_lines)
            page_lines: List[Line] = []
            cur_line: Optional[Line] = None

            for file_line_no, raw_l in enumerate(raw_lines[p_start + 1 : p_end], start=p_start + 2):
                stripped = raw_l.strip()
                if not stripped or stripped.startswith("---"):
                    continue
                parts = raw_l.split("/")
                if len(parts) >= 7 and parts[0] in ("L", "C", "R"):
                    try:
                        d_start = int(parts[2])
                        d_end = int(parts[3])
                    except ValueError:
                        d_start = 0
                        d_end = 0
                        issues.append(
                            KbpIssue(
                                location=f"Page {idx + 1}, File line {file_line_no}",
                                kind="Malformed Display Window",
                                detail=f"Non-integer display window '{parts[2]}/{parts[3]}' → reset to 0/0",
                            )
                        )
                    cur_line = Line(
                        align=parts[0],
                        style=parts[1],
                        display_start=d_start,
                        display_end=d_end,
                        extra_fields=parts[4:],
                    )
                    page_lines.append(cur_line)
                elif cur_line is not None and "/" in raw_l:
                    slash_idx = raw_l.index("/")
                    syl_text = raw_l[:slash_idx]
                    rest = raw_l[slash_idx + 1 :]
                    pad = len(rest) - len(rest.lstrip(" "))
                    t_parts = rest.lstrip(" ").split("/")
                    try:
                        s_start = int(t_parts[0])
                        s_end = int(t_parts[1])
                    except (ValueError, IndexError):
                        fallback_start = cur_line.syllables[-1].end if cur_line.syllables else max(0, cur_line.display_start)
                        s_start = fallback_start
                        s_end = fallback_start + 15
                        issues.append(
                            KbpIssue(
                                location=f"Page {idx + 1}, Line {len(page_lines)}, File line {file_line_no} ('{syl_text.strip()}')",
                                kind="Malformed Syllable Timestamp",
                                detail=f"Could not parse '{rest.strip()}' → recovered as {s_start}–{s_end} cs",
                            )
                        )
                    cur_line.syllables.append(
                        Syllable(
                            text=syl_text,
                            start=s_start,
                            end=s_end,
                            flag=t_parts[2] if len(t_parts) > 2 else "0",
                            pad=pad,
                        )
                    )
            for ln in page_lines:
                ln.refresh_leading_glyph()
            pages.append(Page(lines=page_lines))

        trailing_blanks = 0
        for raw_l in reversed(raw_lines):
            if raw_l == "":
                trailing_blanks += 1
            else:
                break

        st_a = header.styles.get("A")
        st_b = header.styles.get("B")
        detected_duet = bool(
            (st_a and st_b and st_a.name == "Singer 1" and st_b.name == "Singer 2")
            or any(
                (ln.style or "").upper() == "B" and ln.offset_y >= 19
                for pg in pages
                for ln in pg.lines
            )
        )

        proj = cls(
            header=header,
            pages=pages,
            source_path=p,
            trailing_blank_lines=trailing_blanks,
            duet_mode=detected_duet,
            load_issues=issues,
        )
        if auto_repair:
            proj.validate_and_repair()
        return proj

    def validate_and_repair(self) -> List[KbpIssue]:
        """
        Detect and repair non-conforming timestamps (negative times, inverted start/end,
        inverted display windows) in memory. Appends findings to `self.load_issues`.
        """
        new_issues: List[KbpIssue] = []
        for p_idx, page in enumerate(self.pages):
            for l_idx, line in enumerate(page.lines):
                loc_line = f"Page {p_idx + 1}, Line {l_idx + 1}"

                # 1. Check syllables for negative or inverted timestamps
                for s_idx, syl in enumerate(line.syllables):
                    loc_syl = f"{loc_line}, Syl {s_idx + 1} ('{syl.text.strip() or ' '}')"
                    if syl.start < 0 or syl.end < 0:
                        orig = f"{syl.start}–{syl.end} cs"
                        syl.start = max(0, syl.start)
                        syl.end = max(0, syl.end)
                        if syl.end < syl.start:
                            syl.end = syl.start + 15
                        new_issues.append(
                            KbpIssue(
                                location=loc_syl,
                                kind="Negative Timestamp",
                                detail=f"Found {orig} → clamped to {syl.start}–{syl.end} cs",
                            )
                        )

                    if syl.end < syl.start:
                        orig = f"{syl.start}–{syl.end} cs"
                        if syl.end == 0:
                            nxt_start = line.syllables[s_idx + 1].start if s_idx + 1 < len(line.syllables) else 0
                            if nxt_start > syl.start:
                                syl.end = min(syl.start + 30, nxt_start)
                            else:
                                syl.end = syl.start + 15
                            detail = f"End 0 < Start {syl.start} cs → set end to {syl.end} cs"
                        else:
                            syl.start, syl.end = syl.end, syl.start
                            detail = f"End preceded start ({orig}) → swapped to {syl.start}–{syl.end} cs"
                        new_issues.append(
                            KbpIssue(
                                location=loc_syl,
                                kind="Inverted Syllable Time",
                                detail=detail,
                            )
                        )

                # 2. Check line display window for negative or inverted times
                if line.display_start < 0 or line.display_end < 0:
                    orig = f"{line.display_start}–{line.display_end} cs"
                    line.display_start = max(0, line.display_start)
                    line.display_end = max(0, line.display_end)
                    new_issues.append(
                        KbpIssue(
                            location=loc_line,
                            kind="Negative Display Window",
                            detail=f"Found {orig} → clamped to {line.display_start}–{line.display_end} cs",
                        )
                    )

                if line.display_end < line.display_start:
                    orig = f"{line.display_start}–{line.display_end} cs"
                    if line.syllables:
                        line.display_start = max(0, min(line.display_start, line.first_syl_start))
                        line.display_end = max(line.display_start, line.last_syl_end + DEFAULT_CLEAR_HOLD_CS)
                    else:
                        line.display_start, line.display_end = line.display_end, line.display_start
                    new_issues.append(
                        KbpIssue(
                            location=loc_line,
                            kind="Inverted Display Window",
                            detail=f"Display end preceded start ({orig}) → corrected to {line.display_start}–{line.display_end} cs",
                        )
                    )

        self.load_issues.extend(new_issues)
        return new_issues

    def flush_one_cs_gaps(self, max_gap_cs: int = 1) -> int:
        """
        Close tiny KBS snap gaps (default: 1 cs) between consecutive syllables in each line
        by extending `syl.end = next_syl.start`. Every syllable's `start` timestamp stays
        unchanged so there is zero cumulative timing drift.
        Returns the number of gaps flushed.
        """
        flushed = 0
        for page in self.pages:
            for line in page.lines:
                syls = line.syllables
                for i in range(len(syls) - 1):
                    gap = syls[i + 1].start - syls[i].end
                    if 0 < gap <= max_gap_cs:
                        syls[i].end = syls[i + 1].start
                        flushed += 1
        return flushed

    @staticmethod
    def create_backup(path: str | Path) -> Path:
        """
        Save a non-destructive .bak copy of the original file next to it.
        Never overwrites an existing backup (uses .bak, .bak1, .bak2, ...).
        """
        src = Path(path)
        bak = src.with_name(src.name + ".bak")
        counter = 1
        while bak.exists():
            bak = src.with_name(f"{src.name}.bak{counter}")
            counter += 1
        bak.write_bytes(src.read_bytes())
        return bak

    def to_bytes(self) -> bytes:
        out: List[str] = list(self.header.raw_lines)
        for page in self.pages:
            out.extend(page.format_lines())
        out.append(SEPARATOR)
        for _ in range(self.trailing_blank_lines + 1):
            out.append("")
        return "\r\n".join(out).encode("utf-8")

    def save(self, path: Optional[str | Path] = None) -> Path:
        target = Path(path) if path is not None else self.source_path
        if target is None:
            raise ValueError("No target path specified for saving.")
        target.write_bytes(self.to_bytes())
        self.source_path = target
        return target

    def shift_all(self, delta_cs: int) -> None:
        for page in self.pages:
            page.shift(delta_cs)

    def shift_page(self, page_idx: int, delta_cs: int) -> None:
        if 0 <= page_idx < len(self.pages):
            self.pages[page_idx].shift(delta_cs)

    def _adjacent_syllable(
        self, page_idx: int, line_idx: int, syl_idx: int, direction: int
    ) -> Optional[Syllable]:
        """Return the immediately preceding (direction=-1) or following (direction=+1) syllable across lines/pages."""
        if not (0 <= page_idx < len(self.pages)):
            return None
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return None
        line = page.lines[line_idx]
        if not (0 <= syl_idx < len(line.syllables)):
            return None

        if direction < 0:
            if syl_idx > 0:
                return line.syllables[syl_idx - 1]
            for l_i in range(line_idx - 1, -1, -1):
                if page.lines[l_i].syllables:
                    return page.lines[l_i].syllables[-1]
            for p_i in range(page_idx - 1, -1, -1):
                prev_page = self.pages[p_i]
                for l_i in range(len(prev_page.lines) - 1, -1, -1):
                    if prev_page.lines[l_i].syllables:
                        return prev_page.lines[l_i].syllables[-1]
            return None

        if direction > 0:
            if syl_idx + 1 < len(line.syllables):
                return line.syllables[syl_idx + 1]
            for l_i in range(line_idx + 1, len(page.lines)):
                if page.lines[l_i].syllables:
                    return page.lines[l_i].syllables[0]
            for p_i in range(page_idx + 1, len(self.pages)):
                next_page = self.pages[p_i]
                for l_i in range(len(next_page.lines)):
                    if next_page.lines[l_i].syllables:
                        return next_page.lines[l_i].syllables[0]
            return None

        return None

    def snap_syllable_start(self, page_idx: int, line_idx: int, syl_idx: int) -> bool:
        """Snap syllable.start to the previous syllable's end."""
        prev_syl = self._adjacent_syllable(page_idx, line_idx, syl_idx, -1)
        if prev_syl is None:
            return False
        syl = self.pages[page_idx].lines[line_idx].syllables[syl_idx]
        if prev_syl.end >= syl.end:
            return False
        syl.start = prev_syl.end
        return True

    def snap_syllable_end(self, page_idx: int, line_idx: int, syl_idx: int) -> bool:
        """Snap syllable.end to the next syllable's start."""
        next_syl = self._adjacent_syllable(page_idx, line_idx, syl_idx, +1)
        if next_syl is None:
            return False
        syl = self.pages[page_idx].lines[line_idx].syllables[syl_idx]
        if next_syl.start <= syl.start:
            return False
        syl.end = next_syl.start
        return True

    def snap_syllable_both(self, page_idx: int, line_idx: int, syl_idx: int) -> bool:
        """Snap both start (to previous lyric's end) and end (to next lyric's start)."""
        changed_s = self.snap_syllable_start(page_idx, line_idx, syl_idx)
        changed_e = self.snap_syllable_end(page_idx, line_idx, syl_idx)
        return changed_s or changed_e

    def split_line(self, page_idx: int, line_idx: int, syl_idx: int) -> bool:
        """
        Split a line into two consecutive lines on the same page at syl_idx
        (syllables[:syl_idx] stay on the first line; syllables[syl_idx:] move to the new second line).
        """
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return False
        line = page.lines[line_idx]
        if len(line.syllables) < 2:
            return False
        cut = syl_idx if 1 <= syl_idx < len(line.syllables) else len(line.syllables) // 2

        left_syls = line.syllables[:cut]
        right_syls = line.syllables[cut:]
        # Trim trailing word-space on the new end-of-line syllable
        left_syls[-1].text = left_syls[-1].text.rstrip(" ")

        new_line = Line(
            align=line.align,
            style=line.style,
            display_start=line.display_start,
            display_end=max(line.display_end, right_syls[-1].end + DEFAULT_CLEAR_HOLD_CS),
            extra_fields=list(line.extra_fields),
            syllables=right_syls,
        )
        line.syllables = left_syls
        line.display_end = left_syls[-1].end + DEFAULT_CLEAR_HOLD_CS
        page.lines.insert(line_idx + 1, new_line)
        return True

    def join_lines(self, page_idx: int, line_idx: int) -> bool:
        """
        Join line_idx with the next line (on the same page, or the first line of the next page).
        """
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return False

        cur_line = page.lines[line_idx]
        if line_idx + 1 < len(page.lines):
            next_line = page.lines.pop(line_idx + 1)
        elif page_idx + 1 < len(self.pages) and self.pages[page_idx + 1].lines:
            next_page = self.pages[page_idx + 1]
            next_line = next_page.lines.pop(0)
            if not next_page.lines:
                self.pages.pop(page_idx + 1)
        else:
            return False

        if cur_line.syllables and next_line.syllables:
            if not cur_line.syllables[-1].text.endswith(" "):
                cur_line.syllables[-1].text += " "
        cur_line.syllables.extend(next_line.syllables)
        cur_line.display_start = min(cur_line.display_start, next_line.display_start)
        cur_line.display_end = max(cur_line.display_end, next_line.display_end)
        return True

    def pick_duplicate_style(self, lines: Sequence[Line]) -> str:
        """Pick the next style letter after the highest style in ``lines`` (A→B→C→D→E→F→A)."""
        letters = ["A", "B", "C", "D", "E", "F"]
        for k in sorted(self.header.styles.keys()):
            ku = k.upper()
            if ku not in letters:
                letters.append(ku)
        if not lines:
            return "B"
        used = {(ln.style or "A").upper() for ln in lines}
        indices = [letters.index(st) for st in used if st in letters]
        if not indices:
            return "B"
        return letters[(max(indices) + 1) % len(letters)]

    def duplicate_line(
        self,
        page_idx: int,
        line_idx: int,
        target_style: Optional[str] = None,
        shift_cs: int = 0,
    ) -> Optional[Line]:
        """Duplicate a single line on the same page, preserving its original style by default."""
        if not (0 <= page_idx < len(self.pages)):
            return None
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return None
        src = page.lines[line_idx]
        new_line = deepcopy(src)
        if target_style is not None:
            new_line.style = target_style
        if shift_cs:
            new_line.shift(shift_cs)
        page.lines.insert(line_idx + 1, new_line)
        return new_line

    def move_line(
        self,
        page_idx: int,
        line_idx: int,
        delta: int,
    ) -> Optional[Tuple[int, int]]:
        """
        Move a line up (delta < 0) or down (delta > 0) by one slot, crossing page
        boundaries when at the top or bottom of a page. Removes any source page
        left empty. Keeps syllable timestamps unchanged.
        Returns the new (page_idx, line_idx) or None if the line cannot move further.
        """
        if delta == 0 or not (0 <= page_idx < len(self.pages)):
            return None
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return None

        if delta < 0:
            if line_idx > 0:
                page.lines[line_idx - 1], page.lines[line_idx] = (
                    page.lines[line_idx],
                    page.lines[line_idx - 1],
                )
                return (page_idx, line_idx - 1)
            if page_idx > 0:
                prev_page = self.pages[page_idx - 1]
                line = page.lines.pop(0)
                prev_page.lines.append(line)
                new_p_idx = page_idx - 1
                new_l_idx = len(prev_page.lines) - 1
                if not page.lines:
                    self.pages.pop(page_idx)
                return (new_p_idx, new_l_idx)
            return None

        # delta > 0
        if line_idx + 1 < len(page.lines):
            page.lines[line_idx], page.lines[line_idx + 1] = (
                page.lines[line_idx + 1],
                page.lines[line_idx],
            )
            return (page_idx, line_idx + 1)
        if page_idx + 1 < len(self.pages):
            next_page = self.pages[page_idx + 1]
            line = page.lines.pop(line_idx)
            next_page.lines.insert(0, line)
            if not page.lines:
                self.pages.pop(page_idx)
                new_p_idx = page_idx
            else:
                new_p_idx = page_idx + 1
            return (new_p_idx, 0)
        return None

    def duplicate_page(
        self,
        page_number: int,          # 1-based page number (e.g. 8)
        gap_cs: int = 1,
        lead_in_cs: int = DEFAULT_LEAD_IN_CS,
        clear_hold_cs: int = DEFAULT_CLEAR_HOLD_CS,
        fix_lingering_clears: bool = True,
        target_style: Optional[str] = None,
        in_place: bool = False,
    ) -> Page:
        """
        Duplicate a page (1-based) and insert it immediately after the source page.
        When `in_place=True`, keeps the exact same timestamps so the duplicated page
        appears directly beneath the source page ready to be dragged into position.
        """
        src_idx = page_number - 1
        if not (0 <= src_idx < len(self.pages)):
            raise IndexError(f"Page {page_number} out of range (1..{len(self.pages)}).")
        src = self.pages[src_idx]

        if fix_lingering_clears and not in_place:
            for line in src.lines:
                if line.display_end - line.last_syl_end > 200:
                    line.display_end = line.last_syl_end + clear_hold_cs

        new_page = deepcopy(src)
        if not in_place:
            dt = (src.last_syl_end + gap_cs) - src.first_syl_start
            new_page.shift(dt)

        if target_style is not None:
            for line in new_page.lines:
                line.style = target_style

        if not in_place:
            ideal_start = max(0, new_page.first_syl_start - lead_in_cs)
            prev_line_start = 0
            for i, line in enumerate(new_page.lines):
                prev_row_clear = (src.lines[i].display_end + 1) if i < len(src.lines) else 0
                line.display_start = max(ideal_start, prev_row_clear, prev_line_start)
                if line.display_start > line.first_syl_start:
                    line.display_start = line.first_syl_start
                prev_line_start = line.display_start
                if line.last_syl_end > line.display_end:
                    line.display_end = line.last_syl_end + clear_hold_cs

        self.pages.insert(src_idx + 1, new_page)
        return new_page

    def add_line(
        self,
        page_idx: int,
        after_line_idx: int,
        syl_texts: Sequence[str],
        start_cs: Optional[int] = None,
        duration_cs: int = 30,
        style: str = "A",
        align: str = "C",
    ) -> Optional[Line]:
        """Insert a new synced line on `page_idx` right after `after_line_idx`."""
        if not (0 <= page_idx < len(self.pages)):
            return None
        cleaned = [t for t in syl_texts if t]
        if not cleaned:
            cleaned = ["New"]
        page = self.pages[page_idx]
        insert_at = max(0, min(len(page.lines), after_line_idx + 1))

        if start_cs is None:
            if 0 <= after_line_idx < len(page.lines) and page.lines[after_line_idx].syllables:
                t0 = page.lines[after_line_idx].last_syl_end + 10
            elif page.lines:
                t0 = page.last_syl_end + 10
            else:
                t0 = 100
        else:
            t0 = max(0, int(start_cs))

        dur = max(5, int(duration_cs))
        syls: List[Syllable] = []
        cur = t0
        for txt in cleaned:
            syls.append(Syllable(text=txt, start=cur, end=cur + dur))
            cur += dur

        new_line = Line(
            align=align,
            style=style,
            display_start=max(0, t0 - DEFAULT_LEAD_IN_CS),
            display_end=cur + DEFAULT_CLEAR_HOLD_CS,
            syllables=syls,
        )
        page.lines.insert(insert_at, new_line)
        return new_line

    def add_blank_line(self, page_idx: int, insert_idx: int) -> Optional[Line]:
        """Insert a blank 0/0 layout-spacer line at `insert_idx` on `page_idx`."""
        if not (0 <= page_idx < len(self.pages)):
            return None
        page = self.pages[page_idx]
        at = max(0, min(len(page.lines), insert_idx))
        blank = Line(
            align="C",
            style="A",
            display_start=0,
            display_end=0,
            syllables=[Syllable(text="", start=0, end=0)],
        )
        page.lines.insert(at, blank)
        return blank

    def delete_line(self, page_idx: int, line_idx: int) -> bool:
        """Delete a line (removes empty page if >1 page exists; keeps at least 1 line in project)."""
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return False
        total_lines = sum(len(p.lines) for p in self.pages)
        if total_lines <= 1:
            return False
        del page.lines[line_idx]
        if not page.lines and len(self.pages) > 1:
            del self.pages[page_idx]
        return True

    def add_page(
        self,
        after_page_idx: int,
        lines_syls: Sequence[Sequence[str]],
        start_cs: Optional[int] = None,
        duration_cs: int = 30,
        style: str = "A",
        align: str = "C",
    ) -> Page:
        """Insert a new page after `after_page_idx` with one or more lines of syllables."""
        insert_at = max(0, min(len(self.pages), after_page_idx + 1))
        if start_cs is None:
            if 0 <= after_page_idx < len(self.pages) and self.pages[after_page_idx].lines:
                t0 = self.pages[after_page_idx].last_syl_end + 50
            else:
                t0 = 100
        else:
            t0 = max(0, int(start_cs))

        dur = max(5, int(duration_cs))
        built_lines: List[Line] = []
        cur = t0
        for raw_syls in lines_syls:
            cleaned = [t for t in raw_syls if t]
            if not cleaned:
                continue
            syls: List[Syllable] = []
            for txt in cleaned:
                syls.append(Syllable(text=txt, start=cur, end=cur + dur))
                cur += dur
            built_lines.append(
                Line(
                    align=align,
                    style=style,
                    display_start=max(0, t0 - DEFAULT_LEAD_IN_CS),
                    display_end=cur + DEFAULT_CLEAR_HOLD_CS,
                    syllables=syls,
                )
            )
            cur += 20

        if not built_lines:
            built_lines.append(
                Line(
                    align=align,
                    style=style,
                    display_start=max(0, t0 - DEFAULT_LEAD_IN_CS),
                    display_end=t0 + dur + DEFAULT_CLEAR_HOLD_CS,
                    syllables=[Syllable(text="New", start=t0, end=t0 + dur)],
                )
            )

        new_page = Page(lines=built_lines)
        self.pages.insert(insert_at, new_page)
        return new_page

    def split_page(self, page_idx: int, line_idx: int) -> bool:
        """Split `page_idx` into two pages at `line_idx` (lines[:cut] stay; lines[cut:] move to new page)."""
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if len(page.lines) < 2:
            return False
        cut = line_idx if 1 <= line_idx < len(page.lines) else max(1, len(page.lines) // 2)
        new_page = Page(lines=page.lines[cut:])
        page.lines = page.lines[:cut]
        self.pages.insert(page_idx + 1, new_page)
        return True

    def join_pages(self, page_idx: int) -> bool:
        """Merge `page_idx` with `page_idx + 1` into a single page."""
        if not (0 <= page_idx < len(self.pages) - 1):
            return False
        nxt = self.pages.pop(page_idx + 1)
        self.pages[page_idx].lines.extend(nxt.lines)
        return True

    def delete_page(self, page_idx: int) -> bool:
        """Delete `page_idx` (keeps at least 1 page in the project)."""
        if len(self.pages) <= 1 or not (0 <= page_idx < len(self.pages)):
            return False
        del self.pages[page_idx]
        return True

    def _row_pitch(self) -> int:
        line_h = 19
        spacing = self.header.margins[3] if len(self.header.margins) >= 4 else 12
        return max(line_h, spacing + line_h)

    def line_visual_y(self, line_idx: int, line: Line) -> int:
        return line_idx * self._row_pitch() + line.offset_y

    def line_duet_zone(self, line_idx: int, line: Line) -> str:
        """Return 'bottom' if `line` renders in the Bottom Half (Rows 4–6), else 'top'."""
        return "bottom" if self.line_visual_y(line_idx, line) >= 3 * self._row_pitch() else "top"

    @staticmethod
    def _line_duet_group(line: Line) -> str:
        st = (line.style or "A").upper()
        if st in ("A", "D"):
            return "A"
        if st in ("B", "E"):
            return "B"
        return "C"

    def _duet_page_fits(self, page: Page) -> bool:
        non_spacers = page.non_spacer_lines
        if len(non_spacers) > 6:
            return False
        n_a = sum(1 for ln in non_spacers if self._line_duet_group(ln) == "A")
        n_b = sum(1 for ln in non_spacers if self._line_duet_group(ln) == "B")
        if n_a == 0 and n_b == 0:
            return True
        return n_a <= 3 and n_b <= 3 and len(non_spacers) <= 6

    @staticmethod
    def _chunk_time_span(lines: Sequence[Line]) -> Tuple[int, int]:
        timed = [ln for ln in lines if not ln.is_spacer and ln.syllables]
        if not timed:
            return (0, 0)
        return (min(ln.first_syl_start for ln in timed), max(ln.last_syl_end for ln in timed))

    @classmethod
    def _pick_best_bucket(
        cls,
        avail: List[Dict[str, List[Line]]],
        anchor_keys: Tuple[str, ...],
        target_span: Tuple[int, int],
    ) -> Dict[str, List[Line]]:
        """
        Pair `target_span` with the earliest bucket during which `target_span` begins singing
        (so a response starting at 13.0s lands on the 10.0s–15.8s page, while one starting at
        17.0s lands on the 16.0s–21.8s page).
        """
        t_start, _ = target_span
        containing = []
        for bk in avail:
            lines: List[Line] = []
            for k in anchor_keys:
                lines.extend(bk[k])
            b_start, b_end = cls._chunk_time_span(lines)
            if t_start <= b_end:
                containing.append((b_start, bk))
        if containing:
            containing.sort(key=lambda item: item[0])
            return containing[0][1]
        return min(
            avail,
            key=lambda bk: abs(
                cls._chunk_time_span([ln for k in anchor_keys for ln in bk[k]])[0] - t_start
            ),
        )

    def _split_duet_page(self, page: Page) -> List[Page]:
        non_spacers = page.non_spacer_lines
        if not non_spacers:
            return [page]
        a_lines = [ln for ln in non_spacers if self._line_duet_group(ln) == "A"]
        b_lines = [ln for ln in non_spacers if self._line_duet_group(ln) == "B"]
        c_lines = [ln for ln in non_spacers if self._line_duet_group(ln) == "C"]

        if not a_lines and not b_lines:
            return [Page(lines=non_spacers[i : i + 6]) for i in range(0, len(non_spacers), 6)]

        a_chunks = [a_lines[i : i + 3] for i in range(0, len(a_lines), 3)]
        b_chunks = [b_lines[i : i + 3] for i in range(0, len(b_lines), 3)]
        c_chunks = [c_lines[i : i + 3] for i in range(0, len(c_lines), 3)]

        buckets: List[Dict[str, List[Line]]] = []
        if len(a_chunks) >= len(b_chunks) and a_chunks:
            buckets = [{"A": list(ch), "B": [], "C": []} for ch in a_chunks]
            for b_ch in b_chunks:
                b_span = self._chunk_time_span(b_ch)
                avail = [bk for bk in buckets if not bk["B"]]
                target = self._pick_best_bucket(avail, ("A",), b_span)
                target["B"] = list(b_ch)
        elif b_chunks:
            buckets = [{"A": [], "B": list(ch), "C": []} for ch in b_chunks]
            for a_ch in a_chunks:
                a_span = self._chunk_time_span(a_ch)
                avail = [bk for bk in buckets if not bk["A"]]
                target = self._pick_best_bucket(avail, ("B",), a_span)
                target["A"] = list(a_ch)

        for c_ch in c_chunks:
            c_span = self._chunk_time_span(c_ch)
            avail_c = [
                bk
                for bk in buckets
                if not bk["C"]
                and (len(bk["A"]) + len(bk["B"]) + len(c_ch) <= 6)
                and (len(bk["A"]) + len(c_ch) <= 3 or len(bk["B"]) + len(c_ch) <= 3)
            ]
            if avail_c:
                target = self._pick_best_bucket(avail_c, ("A", "B"), c_span)
                target["C"] = list(c_ch)
            else:
                buckets.append({"A": [], "B": [], "C": list(c_ch)})

        buckets.sort(key=lambda bk: self._chunk_time_span(bk["A"] + bk["B"] + bk["C"])[0])
        orig_pos = {id(ln): idx for idx, ln in enumerate(non_spacers)}
        out_pages: List[Page] = []
        for bk in buckets:
            combined = bk["A"] + bk["B"] + bk["C"]
            combined.sort(key=lambda ln: orig_pos.get(id(ln), 0))
            if combined:
                out_pages.append(Page(lines=combined))
        return out_pages or [page]

    def apply_duet_zone_offsets(self) -> None:
        """
        Assign native KBP `down` (`offset_y`) positions on every page for Split-Screen Duet Mode:
        - Top Zone (Rows 1–3, slots 0..2): reserved for [A] Singer 1
        - Bottom Zone (Rows 4–6, slots 3..5): reserved for [B] Singer 2
        - [C] Together: uses full screen (slots 0..5) when alone, or fills whichever zone is free!
        """
        pitch = self._row_pitch()
        for page in self.pages:
            a_idx: List[int] = []
            b_idx: List[int] = []
            c_idx: List[int] = []
            for i, ln in enumerate(page.lines):
                if ln.is_spacer:
                    ln.set_offset_y(0)
                    continue
                grp = self._line_duet_group(ln)
                if grp == "A":
                    a_idx.append(i)
                elif grp == "B":
                    b_idx.append(i)
                else:
                    c_idx.append(i)

            slot_for: Dict[int, int] = {}
            if not a_idx and not b_idx:
                for k, i in enumerate(c_idx):
                    slot_for[i] = k
            elif not c_idx:
                for k, i in enumerate(a_idx):
                    slot_for[i] = k
                for k, i in enumerate(b_idx):
                    slot_for[i] = 3 + k
            elif a_idx and not b_idx:
                for k, i in enumerate(a_idx):
                    slot_for[i] = k
                c_start = 3 if len(c_idx) <= 3 else max(len(a_idx), 6 - len(c_idx))
                for k, i in enumerate(c_idx):
                    slot_for[i] = c_start + k
            elif b_idx and not a_idx:
                for k, i in enumerate(c_idx):
                    slot_for[i] = k
                b_start = max(3, len(c_idx))
                for k, i in enumerate(b_idx):
                    slot_for[i] = b_start + k
            else:
                # A, B, and C all present on the same page
                for k, i in enumerate(a_idx):
                    slot_for[i] = k
                top_free = max(0, 3 - len(a_idx))
                if c_idx[0] < b_idx[0] and top_free > 0:
                    c_top = c_idx[:top_free]
                    c_bot = c_idx[top_free:]
                else:
                    bot_free = max(0, 3 - len(b_idx))
                    c_bot = c_idx[:bot_free]
                    c_top = c_idx[bot_free:]
                for k, i in enumerate(c_top):
                    slot_for[i] = len(a_idx) + k
                bot_combined = sorted(b_idx + c_bot)
                for k, i in enumerate(bot_combined):
                    slot_for[i] = 3 + k

            for i, slot in slot_for.items():
                page.lines[i].set_offset_y((slot - i) * pitch)

    def apply_duet_layout(self, split_overflow_pages: bool = True) -> None:
        """
        Enable Duet Mode layout:
        1) Splits any overflowing page (>3 [A] or >3 [B] or >6 total lines) into balanced subpages.
        2) Assigns Top ([A]) / Bottom ([B]) / free-zone ([C]) vertical `offset_y` coordinates.
        3) Runs Duet-aware Call-and-Response window optimization.
        """
        self.duet_mode = True
        if split_overflow_pages:
            new_pages: List[Page] = []
            for pg in self.pages:
                if self._duet_page_fits(pg):
                    new_pages.append(pg)
                else:
                    new_pages.extend(self._split_duet_page(pg))
            self.pages = new_pages
        self.apply_duet_zone_offsets()
        self.optimize_windows()

    def clear_duet_layout(self) -> None:
        """Disable Duet Mode layout, reset all line `offset_y` values to 0, and re-optimize windows."""
        self.duet_mode = False
        for pg in self.pages:
            for ln in pg.lines:
                ln.set_offset_y(0)
        self.optimize_windows()

    def _row_clear_constraint(self, prev_page: Page, cur_page: Page, cur_row_idx: int) -> int:
        """
        Compute the earliest centisecond cur_row_idx on cur_page can appear without
        visually overlapping an uncleared row from prev_page in KBS/ASS top-anchored coordinates.
        """
        if not prev_page.lines or not (0 <= cur_row_idx < len(cur_page.lines)):
            return 0
        line_h = 19
        pitch = self._row_pitch()
        cur_y = cur_row_idx * pitch + cur_page.lines[cur_row_idx].offset_y

        earliest = 0
        for p_idx, p_line in enumerate(prev_page.lines):
            if p_line.is_spacer:
                continue
            p_y = p_idx * pitch + p_line.offset_y
            if abs(p_y - cur_y) < line_h:
                earliest = max(earliest, p_line.display_end + 1)
        return earliest

    def _prior_pages_row_clear_constraint(self, page_idx: int, cur_row_idx: int) -> int:
        """Check row-clear constraint across prev_page (solo) or all earlier pages (duet overlapping zones)."""
        cur_page = self.pages[page_idx]
        if page_idx <= 0:
            return 0
        if not self.duet_mode:
            return self._row_clear_constraint(self.pages[page_idx - 1], cur_page, cur_row_idx)
        earliest = 0
        for prev_i in range(page_idx):
            earliest = max(earliest, self._row_clear_constraint(self.pages[prev_i], cur_page, cur_row_idx))
        return earliest

    def _next_overlapping_page_ideal_start(
        self,
        page_idx: int,
        row_idx: int,
        lead_in_cs: int,
        lead_in_by_page: Optional[Dict[int, int]] = None,
    ) -> Optional[int]:
        """Find the ideal_start of the next page that visually overlaps `row_idx` on `page_idx`."""
        line_h = 19
        pitch = self._row_pitch()
        cur_y = row_idx * pitch + self.pages[page_idx].lines[row_idx].offset_y
        for nxt_i in range(page_idx + 1, len(self.pages)):
            nxt_pg = self.pages[nxt_i]
            if not nxt_pg.timed_lines:
                continue
            overlaps = any(
                not ln.is_spacer and abs((li * pitch + ln.offset_y) - cur_y) < line_h
                for li, ln in enumerate(nxt_pg.lines)
            )
            if overlaps or not self.duet_mode:
                nxt_lead = lead_in_by_page.get(nxt_i, lead_in_cs) if lead_in_by_page else lead_in_cs
                return max(0, nxt_pg.first_syl_start - nxt_lead)
        return None

    @staticmethod
    def _fixed_line_start_cap(page: Page, l_idx: int) -> int:
        """Latest allowed display_start for a fixed-text line on `page`."""
        for candidate in page.lines[l_idx + 1 :]:
            if not candidate.is_spacer and not candidate.is_fixed and candidate.syllables:
                return candidate.first_syl_start
        if page.timed_lines:
            return page.first_syl_start
        return page.lines[l_idx].display_end

    def reset_windows(
        self,
        page_indices: Optional[Sequence[int]] = None,
        lead_in_cs: int = DEFAULT_LEAD_IN_CS,
        clear_hold_cs: int = DEFAULT_CLEAR_HOLD_CS,
        lead_in_by_page: Optional[Dict[int, int]] = None,
    ) -> None:
        """
        Reset Display/Remove windows to standard KBS defaults:
        - Blank spacer lines stay at 0–0 and do not affect page timing or row clears.
        - Fixed-text lines (lowercase style) stay visible until the page finishes.
        - Timed lines get display_end = line.last_syl_end + clear_hold_cs (default +50 cs)
          and display_start = max(page.first_syl_start - lead_in_cs, prev_overlapping_row_end + 1, prev_line_start).
        - In Duet Mode, tracks Top and Bottom zones independently and pulls Call-and-Response lines into
          unused screen halves so both singers see the exchange appear together.
        """
        target_set = set(range(len(self.pages))) if page_indices is None else set(page_indices)

        # First pass: reset display_end on targeted pages
        for p_idx in sorted(target_set):
            if 0 <= p_idx < len(self.pages):
                page = self.pages[p_idx]
                page_end_cs = (page.last_syl_end + clear_hold_cs) if page.timed_lines else None
                for l_idx, line in enumerate(page.lines):
                    if line.is_spacer:
                        line.display_start = 0
                        line.display_end = 0
                    elif line.is_fixed:
                        if page_end_cs is not None:
                            end_cs = page_end_cs
                            # Clear fixed headers right before the next overlapping page wants to appear
                            # so they never overlap the next page or choke its lead-in time.
                            next_ideal_start = self._next_overlapping_page_ideal_start(
                                p_idx, l_idx, lead_in_cs, lead_in_by_page=lead_in_by_page
                            )
                            if next_ideal_start is not None and next_ideal_start - 1 > page.first_syl_start:
                                end_cs = min(end_cs, next_ideal_start - 1)
                            line.display_end = end_cs
                        else:
                            line.display_end = max(line.display_end, line.last_syl_end + clear_hold_cs)
                    else:
                        line.display_end = line.last_syl_end + clear_hold_cs

        # Second pass: compute display_start respecting previous page row clears (and Duet call-and-response)
        for p_idx in sorted(target_set):
            if not (0 <= p_idx < len(self.pages)):
                continue
            page = self.pages[p_idx]
            prev_page = self.pages[p_idx - 1] if p_idx > 0 else None
            p_lead = lead_in_by_page.get(p_idx, lead_in_cs) if lead_in_by_page else lead_in_cs
            ideal_start = max(0, page.first_syl_start - p_lead)

            prev_zones = (
                {self.line_duet_zone(i, ln) for i, ln in enumerate(prev_page.lines) if not ln.is_spacer}
                if (self.duet_mode and prev_page is not None)
                else set()
            )
            allow_call_response_pull = bool(
                self.duet_mode
                and prev_page is not None
                and prev_page.timed_lines
                and page.timed_lines
                and page.first_syl_start <= prev_page.last_syl_end + 250
            )

            prev_line_start = 0
            prev_zone_start: Dict[str, int] = {"top": 0, "bottom": 0}
            for l_idx, line in enumerate(page.lines):
                if line.is_spacer:
                    line.display_start = 0
                    line.display_end = 0
                    continue
                row_clear = self._prior_pages_row_clear_constraint(p_idx, l_idx)
                if self.duet_mode:
                    z = self.line_duet_zone(l_idx, line)
                    line_ideal = ideal_start
                    if allow_call_response_pull and z not in prev_zones and prev_page is not None:
                        line_ideal = min(line_ideal, prev_page.display_start)
                    start = max(line_ideal, row_clear, prev_zone_start[z])
                else:
                    start = max(ideal_start, row_clear, prev_line_start)
                # Never appear after the line's own first syllable (or the next timed line for fixed labels)
                cap = self._fixed_line_start_cap(page, l_idx) if line.is_fixed else line.first_syl_start
                start = min(start, cap)
                line.display_start = max(0, start)
                if line.display_end < line.display_start:
                    line.display_end = line.display_start
                if self.duet_mode:
                    prev_zone_start[self.line_duet_zone(l_idx, line)] = line.display_start
                else:
                    prev_line_start = line.display_start

    def is_optimized_lead_in_page(
        self,
        page_idx: int,
        marker: str = DEFAULT_LEAD_IN_MARKER,
        gap_threshold_cs: int = DEFAULT_GAP_THRESHOLD_CS,
    ) -> bool:
        """
        True when page_idx starts with a lead-in glyph (e.g. ➣➣➣, ••, ▅▅▅▅▅▅▅, ➤) on its first timed line
        AND follows a full-song instrumental gap (first page, or silence >= gap_threshold_cs since all prior pages).
        Mid-page markers never trigger this.
        """
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if not page.starts_with_marker(marker):
            return False
        if page_idx == 0:
            return True
        prev_ends = [p.last_syl_end for p in self.pages[:page_idx] if p.timed_lines]
        if not prev_ends:
            return True
        gap = page.first_syl_start - max(prev_ends)
        return gap >= gap_threshold_cs

    def optimize_windows(
        self,
        page_indices: Optional[Sequence[int]] = None,
        marker: str = DEFAULT_LEAD_IN_MARKER,
        optimized_lead_in_cs: int = DEFAULT_OPTIMIZED_LEAD_IN_CS,
        gap_threshold_cs: int = DEFAULT_GAP_THRESHOLD_CS,
        default_lead_in_cs: int = DEFAULT_LEAD_IN_CS,
        clear_hold_cs: int = DEFAULT_CLEAR_HOLD_CS,
        reset_non_marker_pages: bool = True,
    ) -> List[int]:
        """
        Apply 'Optimized Windows':
        - Optionally resets targeted pages to standard defaults (including Duet Call-and-Response rules) first.
        - On pages where the first timed line starts with a lead-in glyph AND there is a full
          instrumental gap before the page (>= gap_threshold_cs, or Page 1), shrinks the page's
          display_start to `page.first_syl_start - optimized_lead_in_cs` (~25 cs).
        Returns the 0-based indices of pages that received the shrunk lead-in window.
        """
        target_list = list(range(len(self.pages))) if page_indices is None else sorted(set(page_indices))
        optimized_pages = [
            p_idx
            for p_idx in target_list
            if self.is_optimized_lead_in_page(p_idx, marker=marker, gap_threshold_cs=gap_threshold_cs)
        ]
        lead_map = {p_idx: optimized_lead_in_cs for p_idx in optimized_pages}

        if reset_non_marker_pages:
            self.reset_windows(
                page_indices=target_list,
                lead_in_cs=default_lead_in_cs,
                clear_hold_cs=clear_hold_cs,
                lead_in_by_page=lead_map,
            )
            return optimized_pages

        for p_idx in optimized_pages:
            page = self.pages[p_idx]
            ideal_start = max(0, page.first_syl_start - optimized_lead_in_cs)
            prev_line_start = 0
            prev_zone_start: Dict[str, int] = {"top": 0, "bottom": 0}
            for l_idx, line in enumerate(page.lines):
                if line.is_spacer:
                    line.display_start = 0
                    line.display_end = 0
                    continue
                row_clear = self._prior_pages_row_clear_constraint(p_idx, l_idx)
                z_prev = prev_zone_start[self.line_duet_zone(l_idx, line)] if self.duet_mode else prev_line_start
                start = max(ideal_start, row_clear, z_prev)
                cap = self._fixed_line_start_cap(page, l_idx) if line.is_fixed else line.first_syl_start
                start = min(start, cap)
                line.display_start = max(0, start)
                if self.duet_mode:
                    prev_zone_start[self.line_duet_zone(l_idx, line)] = line.display_start
                else:
                    prev_line_start = line.display_start

        return optimized_pages
