from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


SEPARATOR = "-----------------------------"
DEFAULT_LEAD_IN_CS = 300
DEFAULT_CLEAR_HOLD_CS = 50
DEFAULT_OPTIMIZED_LEAD_IN_CS = 25
DEFAULT_GAP_THRESHOLD_CS = 300
DEFAULT_LEAD_IN_MARKER = "➣➣➣"


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


@dataclass
class Syllable:
    text: str          # e.g. "(True " or "love)" — trailing space marks end of word
    start: int         # centiseconds (1/100 s)
    end: int           # centiseconds
    flag: str = "0"    # KBS syllable flag (usually "0")
    pad: int = 0       # spaces after "/" to preserve original column alignment

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

        left = Syllable(text=left_text, start=syl.start, end=mid_cs, flag=syl.flag)
        right = Syllable(text=right_text, start=mid_cs, end=syl.end, flag=syl.flag)
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
        """
        cleaned = [t for t in texts if t]
        if not cleaned:
            return False
        insert_idx = max(0, min(len(self.syllables), insert_idx))
        count = len(cleaned)
        dur = max(5, int(duration_cs))

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
            step = max(2, min(dur, avail // count)) if nxt_start > t0 else dur
        elif insert_idx >= len(self.syllables):
            t0 = self.syllables[-1].end
            step = dur
        else:
            prev_end = self.syllables[insert_idx - 1].end
            nxt_start = self.syllables[insert_idx].start
            gap = nxt_start - prev_end
            if gap >= count * 4:
                t0 = prev_end
                step = min(dur, gap // count)
            else:
                t0 = prev_end
                step = dur

        new_syls: List[Syllable] = []
        cur = t0
        for txt in cleaned:
            new_syls.append(Syllable(text=txt, start=cur, end=cur + step))
            cur += step

        self.syllables[insert_idx:insert_idx] = new_syls
        if new_syls[0].start < self.display_start:
            self.display_start = new_syls[0].start
        if new_syls[-1].end > self.display_end:
            self.display_end = new_syls[-1].end + DEFAULT_CLEAR_HOLD_CS
        return True

    def format_lines(self) -> List[str]:
        header = "/".join(
            [self.align, self.style, str(self.display_start), str(self.display_end), *self.extra_fields]
        )
        return [header, *[s.format() for s in self.syllables], ""]


@dataclass
class Page:
    lines: List[Line] = field(default_factory=list)

    @property
    def first_syl_start(self) -> int:
        starts = [line.first_syl_start for line in self.lines if line.syllables]
        return min(starts) if starts else 0

    @property
    def last_syl_end(self) -> int:
        ends = [line.last_syl_end for line in self.lines if line.syllables]
        return max(ends) if ends else 0

    @property
    def display_start(self) -> int:
        return min((line.display_start for line in self.lines), default=0)

    @property
    def display_end(self) -> int:
        return max((line.display_end for line in self.lines), default=0)

    def starts_with_marker(self, marker: str = DEFAULT_LEAD_IN_MARKER) -> bool:
        """True if Line 1's first syllable begins with the lead-in marker (e.g. ➣➣➣)."""
        if not self.lines or not self.lines[0].syllables:
            return False
        first_text = self.lines[0].syllables[0].text.strip()
        return first_text.startswith(marker)

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
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")

        raw_lines = text.splitlines()
        page_indices = [i for i, l in enumerate(raw_lines) if l.strip() == "PAGEV2"]
        if not page_indices:
            raise ValueError("No PAGEV2 sections found (file may still be unsynced LYRICSV2).")

        first_sep = page_indices[0] - 1
        while first_sep > 0 and not raw_lines[first_sep].startswith("---"):
            first_sep -= 1
        header_lines = raw_lines[:first_sep]
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
            pages.append(Page(lines=page_lines))

        trailing_blanks = 0
        for raw_l in reversed(raw_lines):
            if raw_l == "":
                trailing_blanks += 1
            else:
                break

        proj = cls(
            header=header,
            pages=pages,
            source_path=p,
            trailing_blank_lines=trailing_blanks,
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
        flat: List[Tuple[int, int, int, Syllable]] = []
        target_pos = -1
        for p_i, page in enumerate(self.pages):
            for l_i, line in enumerate(page.lines):
                for s_i, syl in enumerate(line.syllables):
                    if p_i == page_idx and l_i == line_idx and s_i == syl_idx:
                        target_pos = len(flat)
                    flat.append((p_i, l_i, s_i, syl))
        if target_pos < 0:
            return None
        adj_pos = target_pos + direction
        if 0 <= adj_pos < len(flat):
            return flat[adj_pos][3]
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
        """Pick [B] for duplicated [A] tracks, or [C] if [B] is already in use."""
        styles = {ln.style for ln in lines}
        if "B" in styles:
            return "C"
        return "B"

    def duplicate_line(
        self,
        page_idx: int,
        line_idx: int,
        target_style: Optional[str] = None,
        shift_cs: int = 0,
    ) -> Optional[Line]:
        """Duplicate a single line on the same page, assigning style [B]/[C] by default."""
        if not (0 <= page_idx < len(self.pages)):
            return None
        page = self.pages[page_idx]
        if not (0 <= line_idx < len(page.lines)):
            return None
        src = page.lines[line_idx]
        new_line = deepcopy(src)
        new_line.style = target_style if target_style is not None else self.pick_duplicate_style([src])
        if shift_cs:
            new_line.shift(shift_cs)
        page.lines.insert(line_idx + 1, new_line)
        return new_line

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

    def _row_clear_constraint(self, prev_page: Page, cur_page: Page, cur_row_idx: int) -> int:
        """
        Compute the earliest centisecond cur_row_idx on cur_page can appear without
        visually overlapping an uncleared row from prev_page.
        """
        if not prev_page.lines:
            return 0
        n_prev = len(prev_page.lines)
        n_cur = len(cur_page.lines)

        # Direct row match when line counts are equal or prev has at least this row.
        # When cur_page has more rows than prev_page (e.g. 4 -> 6), rows shift vertically
        # in centered layout so we check adjacent overlapping slots too.
        if n_prev == n_cur:
            return prev_page.lines[cur_row_idx].display_end + 1

        # Map each row to normalized vertical band [0, 1]
        cur_top = cur_row_idx / max(1, n_cur)
        cur_bot = (cur_row_idx + 1) / max(1, n_cur)
        earliest = 0
        for p_idx, p_line in enumerate(prev_page.lines):
            p_top = p_idx / max(1, n_prev)
            p_bot = (p_idx + 1) / max(1, n_prev)
            if p_top < cur_bot and p_bot > cur_top:
                earliest = max(earliest, p_line.display_end + 1)
        return earliest

    def reset_windows(
        self,
        page_indices: Optional[Sequence[int]] = None,
        lead_in_cs: int = DEFAULT_LEAD_IN_CS,
        clear_hold_cs: int = DEFAULT_CLEAR_HOLD_CS,
    ) -> None:
        """
        Reset Display/Remove windows to standard KBS defaults:
        - display_end = line.last_syl_end + clear_hold_cs (default +50 cs)
        - display_start = max(page.first_syl_start - lead_in_cs, prev_overlapping_row_end + 1, prev_line_start)
        """
        target_set = set(range(len(self.pages))) if page_indices is None else set(page_indices)

        # First pass: reset display_end on targeted pages
        for p_idx in sorted(target_set):
            if 0 <= p_idx < len(self.pages):
                for line in self.pages[p_idx].lines:
                    line.display_end = line.last_syl_end + clear_hold_cs

        # Second pass: compute display_start respecting previous page row clears
        for p_idx in sorted(target_set):
            if not (0 <= p_idx < len(self.pages)):
                continue
            page = self.pages[p_idx]
            prev_page = self.pages[p_idx - 1] if p_idx > 0 else None
            ideal_start = max(0, page.first_syl_start - lead_in_cs)
            prev_line_start = 0
            for l_idx, line in enumerate(page.lines):
                row_clear = self._row_clear_constraint(prev_page, page, l_idx) if prev_page else 0
                start = max(ideal_start, row_clear, prev_line_start)
                # Never appear after the line's own first syllable
                start = min(start, line.first_syl_start)
                line.display_start = max(0, start)
                prev_line_start = line.display_start

    def is_optimized_lead_in_page(
        self,
        page_idx: int,
        marker: str = DEFAULT_LEAD_IN_MARKER,
        gap_threshold_cs: int = DEFAULT_GAP_THRESHOLD_CS,
    ) -> bool:
        """
        True when page_idx starts with the lead-in marker (e.g. ➣➣➣) on Line 1
        AND follows an instrumental gap (first page, or gap >= gap_threshold_cs).
        Mid-page markers never trigger this.
        """
        if not (0 <= page_idx < len(self.pages)):
            return False
        page = self.pages[page_idx]
        if not page.starts_with_marker(marker):
            return False
        if page_idx == 0:
            return True
        prev_page = self.pages[page_idx - 1]
        gap = page.first_syl_start - prev_page.last_syl_end
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
        - Optionally resets targeted pages to standard defaults first.
        - On pages where Line 1 starts with `marker` (➣➣➣) AND there is an instrumental
          gap before the page (>= gap_threshold_cs, or Page 1), shrinks the page's
          display_start to `page.first_syl_start - optimized_lead_in_cs` (~25 cs).
        Returns the 0-based indices of pages that received the shrunk lead-in window.
        """
        target_list = list(range(len(self.pages))) if page_indices is None else sorted(set(page_indices))

        if reset_non_marker_pages:
            self.reset_windows(
                page_indices=target_list,
                lead_in_cs=default_lead_in_cs,
                clear_hold_cs=clear_hold_cs,
            )

        optimized_pages: List[int] = []
        for p_idx in target_list:
            if not self.is_optimized_lead_in_page(p_idx, marker=marker, gap_threshold_cs=gap_threshold_cs):
                continue
            page = self.pages[p_idx]
            prev_page = self.pages[p_idx - 1] if p_idx > 0 else None
            ideal_start = max(0, page.first_syl_start - optimized_lead_in_cs)
            prev_line_start = 0
            for l_idx, line in enumerate(page.lines):
                row_clear = self._row_clear_constraint(prev_page, page, l_idx) if prev_page else 0
                start = max(ideal_start, row_clear, prev_line_start)
                start = min(start, line.first_syl_start)
                line.display_start = max(0, start)
                prev_line_start = line.display_start
            optimized_pages.append(p_idx)

        return optimized_pages
