from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import (
    DEFAULT_CLEAR_HOLD_CS,
    DEFAULT_LEAD_IN_CS,
    KbpHeader,
    KbpProject,
    Line,
    Page,
    SEPARATOR,
    Syllable,
)


DEFAULT_KBP_HEADER_LINES: List[str] = [
    SEPARATOR,
    "KARAOKE BUILDER STUDIO",
    "www.KaraokeBuilder.com",
    "",
    SEPARATOR,
    "HEADERV2",
    "",
    "'--- Template Information ---",
    "",
    "'Palette Colours (0-15)",
    "  055,FFF,000,E70,940,CFF,033,0DD,077,FCF,303,F3F,818,000,FFF,000",
    "",
    "'Styles (00-19)",
    "'  Number,Name",
    "'  Colour: Text,Outline,Text Wipe,Outline Wipe",
    "'  Font  : Name,Size,Style,Charset",
    "'  Other : Outline*4,Shadow*2,Wiping,Uppercase",
    "",
    "  Style00,Default,1,2,3,4",
    "    Arial,12,B,0",
    "    2,2,2,2,0,0,0,L",
    "",
    "  Style01,Male,5,6,7,8",
    "    Arial,12,B,0",
    "    2,2,2,2,0,0,0,L",
    "",
    "  Style02,Female,9,10,11,12",
    "    Arial,12,B,0",
    "    2,2,2,2,0,0,0,L",
    "",
    "  Style03,Other,4,8,12,14",
    "    Arial,12,B,0",
    "    2,2,2,2,0,0,0,L",
    "",
    "  StyleEnd",
    "",
    "'Margins : L,R,T,Line Spacing",
    "  2,2,7,12",
    "",
    "'Other: Border Colour,Detail Level",
    "  0,2",
    "",
    "'--- Track Information ---",
    "",
    "Status    1",
    "Title     ",
    "Artist    ",
    "Audio     ",
    "BuildFile ",
    "Intro     ",
    "Outro     ",
    "",
    "Comments  ",
    "",
]


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


@dataclass
class TapSyllable:
    text: str
    start_cs: Optional[int] = None
    end_cs: Optional[int] = None

    @property
    def has_start(self) -> bool:
        return self.start_cs is not None

    @property
    def is_synced(self) -> bool:
        return self.start_cs is not None and self.end_cs is not None

    def clear(self) -> None:
        self.start_cs = None
        self.end_cs = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start_cs": self.start_cs,
            "end_cs": self.end_cs,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TapSyllable":
        return cls(
            text=str(data.get("text", "")),
            start_cs=int(data["start_cs"]) if data.get("start_cs") is not None else None,
            end_cs=int(data["end_cs"]) if data.get("end_cs") is not None else None,
        )


@dataclass
class TapLine:
    syllables: List[TapSyllable] = field(default_factory=list)
    style: str = "A"
    align: str = "C"

    @property
    def full_text(self) -> str:
        return "".join(s.text for s in self.syllables)

    @property
    def slash_text(self) -> str:
        """Reconstruct KBS slash-delimited text for editing."""
        out: List[str] = []
        for i, s in enumerate(self.syllables):
            out.append(s.text)
            if i + 1 < len(self.syllables) and not s.text.endswith(" "):
                out.append("/")
        return "".join(out)

    @property
    def sync_state(self) -> str:
        """Return 'none', 'partial', or 'full'."""
        if not self.syllables:
            return "none"
        started = sum(1 for s in self.syllables if s.start_cs is not None)
        if started == 0:
            return "none"
        if started == len(self.syllables) and all(s.end_cs is not None for s in self.syllables):
            return "full"
        return "partial"

    @property
    def first_start_cs(self) -> Optional[int]:
        vals = [s.start_cs for s in self.syllables if s.start_cs is not None]
        return min(vals) if vals else None

    @property
    def last_end_cs(self) -> Optional[int]:
        vals = [s.end_cs for s in self.syllables if s.end_cs is not None]
        if vals:
            return max(vals)
        vals_s = [s.start_cs for s in self.syllables if s.start_cs is not None]
        return max(vals_s) if vals_s else None

    def clear_timing(self) -> None:
        for s in self.syllables:
            s.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "style": self.style,
            "align": self.align,
            "syllables": [s.to_dict() for s in self.syllables],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TapLine":
        return cls(
            style=str(data.get("style", "A")),
            align=str(data.get("align", "C")),
            syllables=[TapSyllable.from_dict(s) for s in data.get("syllables", [])],
        )


@dataclass
class TapPage:
    lines: List[TapLine] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"lines": [ln.to_dict() for ln in self.lines]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TapPage":
        return cls(lines=[TapLine.from_dict(ln) for ln in data.get("lines", [])])


@dataclass
class TapSyncDraft:
    pages: List[TapPage] = field(default_factory=list)
    title: str = ""
    artist: str = ""
    audio_path: str = ""
    vocal_path: str = ""
    template_kbp_path: str = ""
    raw_header_lines: Optional[List[str]] = None
    latency_enabled: bool = True
    latency_cs: int = 4
    pre_roll_cs: int = 150

    def flat_lines(self) -> List[Tuple[int, int, TapLine]]:
        out: List[Tuple[int, int, TapLine]] = []
        for p_idx, page in enumerate(self.pages):
            for l_idx, line in enumerate(page.lines):
                out.append((p_idx, l_idx, line))
        return out

    def flat_syllables(self) -> List[Tuple[int, int, int, TapSyllable]]:
        out: List[Tuple[int, int, int, TapSyllable]] = []
        for p_idx, page in enumerate(self.pages):
            for l_idx, line in enumerate(page.lines):
                for s_idx, syl in enumerate(line.syllables):
                    out.append((p_idx, l_idx, s_idx, syl))
        return out

    def total_syllables(self) -> int:
        return sum(len(ln.syllables) for p in self.pages for ln in p.lines)

    def synced_syllables(self) -> int:
        return sum(1 for p in self.pages for ln in p.lines for s in ln.syllables if s.is_synced)

    def first_unsynced_coord(self) -> Optional[Tuple[int, int, int]]:
        for p_idx, page in enumerate(self.pages):
            for l_idx, line in enumerate(page.lines):
                for s_idx, syl in enumerate(line.syllables):
                    if not syl.has_start:
                        return (p_idx, l_idx, s_idx)
        return None

    def to_lyrics_text(self) -> str:
        page_blocks: List[str] = []
        for page in self.pages:
            lines_txt = [ln.slash_text for ln in page.lines if ln.syllables]
            if lines_txt:
                page_blocks.append("\n".join(lines_txt))
        return "\n\n".join(page_blocks)

    @classmethod
    def from_lyrics_text(cls, text: str) -> "TapSyncDraft":
        """Parse plain slash-delimited lyrics where blank lines separate pages."""
        pages: List[TapPage] = []
        cur_lines: List[TapLine] = []
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped:
                if cur_lines:
                    pages.append(TapPage(lines=cur_lines))
                    cur_lines = []
                continue
            syls = parse_line_to_syllables(stripped)
            if syls:
                cur_lines.append(TapLine(syllables=[TapSyllable(text=t) for t in syls]))
        if cur_lines:
            pages.append(TapPage(lines=cur_lines))
        return cls(pages=pages)

    @classmethod
    def from_kbp_file(cls, path: str | Path) -> "TapSyncDraft":
        """
        Parse an unsynced KBP file (LYRICSV2) or a synced KBP file (PAGEV2) into a TapSyncDraft,
        preserving its HEADERV2 (styles, colors, fonts, title, artist, audio path).
        """
        p = Path(path)
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        raw_lines = text.splitlines()

        lyrics_idx = next((i for i, l in enumerate(raw_lines) if l.strip() == "LYRICSV2"), None)
        if lyrics_idx is not None:
            # Extract header up to the separator preceding LYRICSV2
            sep_idx = lyrics_idx - 1
            while sep_idx > 0 and not raw_lines[sep_idx].startswith("---"):
                sep_idx -= 1
            header_lines = raw_lines[:sep_idx]
            header = KbpHeader.parse(header_lines)

            body_lines: List[str] = []
            for raw_l in raw_lines[lyrics_idx + 1 :]:
                if raw_l.strip().startswith("---"):
                    break
                body_lines.append(raw_l)
            draft = cls.from_lyrics_text("\n".join(body_lines))
            draft.title = header.title
            draft.artist = header.artist
            draft.audio_path = header.audio_path
            draft.raw_header_lines = list(header_lines)
            return draft

        # Fallback: synced PAGEV2 .kbp — import its syllables & existing timings!
        proj = KbpProject.load(p)
        pages: List[TapPage] = []
        for page in proj.pages:
            t_lines: List[TapLine] = []
            for line in page.lines:
                t_syls = [TapSyllable(text=s.text, start_cs=s.start, end_cs=s.end) for s in line.syllables]
                if t_syls:
                    t_lines.append(TapLine(syllables=t_syls, style=line.style, align=line.align))
            if t_lines:
                pages.append(TapPage(lines=t_lines))
        return cls(
            pages=pages,
            title=proj.header.title,
            artist=proj.header.artist,
            audio_path=proj.header.audio_path,
            raw_header_lines=list(proj.header.raw_lines),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "title": self.title,
            "artist": self.artist,
            "audio_path": self.audio_path,
            "vocal_path": self.vocal_path,
            "template_kbp_path": self.template_kbp_path,
            "raw_header_lines": self.raw_header_lines,
            "latency_enabled": self.latency_enabled,
            "latency_cs": self.latency_cs,
            "pre_roll_cs": self.pre_roll_cs,
            "pages": [p.to_dict() for p in self.pages],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TapSyncDraft":
        raw_hdr = data.get("raw_header_lines")
        return cls(
            pages=[TapPage.from_dict(p) for p in data.get("pages", [])],
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            audio_path=str(data.get("audio_path", "")),
            vocal_path=str(data.get("vocal_path", "")),
            template_kbp_path=str(data.get("template_kbp_path", "")),
            raw_header_lines=[str(x) for x in raw_hdr] if isinstance(raw_hdr, list) else None,
            latency_enabled=bool(data.get("latency_enabled", True)),
            latency_cs=int(data.get("latency_cs", 4)),
            pre_roll_cs=int(data.get("pre_roll_cs", 150)),
        )

    def save_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    @classmethod
    def load_json(cls, path: str | Path) -> "TapSyncDraft":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @staticmethod
    def _extract_header_from_kbp(path: str | Path) -> Optional[List[str]]:
        p = Path(path)
        if not p.exists():
            return None
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        raw_lines = text.splitlines()
        marker = next((i for i, l in enumerate(raw_lines) if l.strip() in ("PAGEV2", "LYRICSV2")), None)
        if marker is None:
            return None
        sep = marker - 1
        while sep > 0 and not raw_lines[sep].startswith("---"):
            sep -= 1
        return raw_lines[:sep]

    @staticmethod
    def _set_header_field(raw_lines: List[str], field_name: str, value: str) -> None:
        for i, line in enumerate(raw_lines):
            if line.startswith(field_name):
                rest = line[len(field_name) :]
                pad_len = len(rest) - len(rest.lstrip(" \t"))
                pad = rest[:pad_len] if pad_len > 0 else "   "
                raw_lines[i] = f"{field_name}{pad}{value}"
                return
        raw_lines.append(f"{field_name}     {value}")

    def build_kbp_project(self, default_tail_cs: int = 35) -> KbpProject:
        """
        Convert this TapSyncDraft into a full KbpProject with 0 cs flush borders
        and optimized Display/Remove windows.
        Any unsynced syllables (if building a partial draft) are placed sequentially
        after the last synced timestamp so the resulting KBP is always valid.
        """
        header_lines: Optional[List[str]] = None
        if self.template_kbp_path:
            header_lines = self._extract_header_from_kbp(self.template_kbp_path)
        if header_lines is None and self.raw_header_lines:
            header_lines = list(self.raw_header_lines)
        if header_lines is None:
            header_lines = list(DEFAULT_KBP_HEADER_LINES)

        header = KbpHeader.parse(header_lines)
        if self.title:
            header.title = self.title
            self._set_header_field(header.raw_lines, "Title", self.title)
        if self.artist:
            header.artist = self.artist
            self._set_header_field(header.raw_lines, "Artist", self.artist)
        if self.audio_path:
            header.set_audio_path(self.audio_path)

        built_pages: List[Page] = []
        cursor_cs = 100
        for t_page in self.pages:
            built_lines: List[Line] = []
            for t_line in t_page.lines:
                if not t_line.syllables:
                    continue
                syls: List[Syllable] = []
                n_s = len(t_line.syllables)
                for i, ts in enumerate(t_line.syllables):
                    start = ts.start_cs if ts.start_cs is not None else cursor_cs
                    if ts.end_cs is not None and ts.end_cs > start:
                        end = ts.end_cs
                    elif i + 1 < n_s and t_line.syllables[i + 1].start_cs is not None and t_line.syllables[i + 1].start_cs > start:
                        end = t_line.syllables[i + 1].start_cs
                    else:
                        end = start + max(10, default_tail_cs)
                    syls.append(Syllable(text=ts.text, start=max(0, start), end=max(start + 1, end)))
                    cursor_cs = end

                built_lines.append(
                    Line(
                        align=t_line.align,
                        style=t_line.style,
                        display_start=max(0, syls[0].start - DEFAULT_LEAD_IN_CS),
                        display_end=syls[-1].end + DEFAULT_CLEAR_HOLD_CS,
                        syllables=syls,
                    )
                )
            if built_lines:
                built_pages.append(Page(lines=built_lines))

        proj = KbpProject(header=header, pages=built_pages)
        proj.optimize_windows()
        return proj
