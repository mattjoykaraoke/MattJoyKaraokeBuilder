from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .model import KbpProject, read_kbp_text, suggest_project_filename

OVERFLOW_NO_WRAP = "no_wrap"
OVERFLOW_EVEN_SPLIT = "even_split"
OVERFLOW_TOP_SPLIT = "top_split"
OVERFLOW_BOTTOM_SPLIT = "bottom_split"

OVERFLOW_CHOICES = (
    (OVERFLOW_NO_WRAP, "No Wrap"),
    (OVERFLOW_EVEN_SPLIT, "Even Split"),
    (OVERFLOW_TOP_SPLIT, "Top Split"),
    (OVERFLOW_BOTTOM_SPLIT, "Bottom Split"),
)

RESOLUTION_PRESETS = (
    ("1920 × 1080 (1080p)", 1920, 1080),
    ("1280 × 720 (720p)", 1280, 720),
    ("3840 × 2160 (4K UHD)", 3840, 2160),
)


@dataclass(frozen=True)
class AssExportOptions:
    """Configuration for converting a KBP project/file into an ASS subtitle file."""

    target_x: int = 1920
    target_y: int = 1080
    fade_in: int = 0
    fade_out: int = 0
    overflow: str = OVERFLOW_NO_WRAP


def _import_kbputils():
    try:
        import kbputils  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "kbputils is not installed in this Python environment. "
            "Install it with: pip install kbputils"
        ) from exc
    return kbputils


def _resolve_overflow(kbputils_mod, overflow_key: str):
    ass_overflow = getattr(kbputils_mod, "AssOverflow", None)
    if ass_overflow is not None:
        mapping = {
            OVERFLOW_NO_WRAP: ass_overflow.NO_WRAP,
            OVERFLOW_EVEN_SPLIT: ass_overflow.EVEN_SPLIT,
            OVERFLOW_TOP_SPLIT: ass_overflow.TOP_SPLIT,
            OVERFLOW_BOTTOM_SPLIT: ass_overflow.BOTTOM_SPLIT,
        }
        return mapping.get(overflow_key, ass_overflow.NO_WRAP)
    fallback_ints = {
        OVERFLOW_EVEN_SPLIT: 0,
        OVERFLOW_TOP_SPLIT: 1,
        OVERFLOW_NO_WRAP: 2,
        OVERFLOW_BOTTOM_SPLIT: 3,
    }
    return fallback_ints.get(overflow_key, 2)


def suggest_ass_path(project: KbpProject, fallback_dir: Optional[Path] = None) -> Path:
    """
    Build a sensible default `.ass` output path for `project`.
    Prefers placing the file next to `project.source_path` with the same stem.
    """
    if project.source_path is not None:
        return project.source_path.with_suffix(".ass")

    filename = suggest_project_filename(
        project.header.artist,
        project.header.title,
        ext=".ass",
        fallback_stem="untitled",
    )
    if fallback_dir is not None:
        return fallback_dir / filename
    return Path(filename)


def _kbp_text_to_kbpfile(content: str, filename_hint: str):
    kbputils = _import_kbputils()
    buf = io.StringIO(content)
    buf.filename = filename_hint  # type: ignore[attr-defined]
    kbp_file = kbputils.KBPFile(buf)
    kbp_file.filename = filename_hint
    return kbp_file


def load_kbp_for_ass(kbp_path: str | Path):
    """Load a `.kbp` file from disk using UTF-8 with CP1252 fallback."""
    path = Path(kbp_path)
    content = read_kbp_text(path)
    return _kbp_text_to_kbpfile(content, str(path))


def project_to_kbpfile(project: KbpProject):
    """Serialize an in-memory `KbpProject` into a `kbputils.KBPFile`."""
    content = project.to_bytes().decode("utf-8")
    hint = str(project.source_path) if project.source_path is not None else "project.kbp"
    return _kbp_text_to_kbpfile(content, hint)


def _write_ass_document(kbp_file, output_path: str | Path, options: AssExportOptions) -> Path:
    kbputils = _import_kbputils()
    overflow_val = _resolve_overflow(kbputils, options.overflow)
    converter = kbputils.AssConverter(
        kbp_file,
        target_x=int(options.target_x),
        target_y=int(options.target_y),
        fade_in=int(options.fade_in),
        fade_out=int(options.fade_out),
        overflow=overflow_val,
    )
    doc = converter.ass_document()

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf_8_sig") as fh:
        doc.dump_file(fh)
    return out_path


def convert_project_to_ass(
    project: KbpProject,
    output_path: str | Path,
    options: Optional[AssExportOptions] = None,
) -> Path:
    """Convert an in-memory `KbpProject` directly to an `.ass` subtitle file."""
    opts = options if options is not None else AssExportOptions()
    kbp_file = project_to_kbpfile(project)
    return _write_ass_document(kbp_file, output_path, opts)


def convert_kbp_file_to_ass(
    kbp_path: str | Path,
    output_path: str | Path,
    options: Optional[AssExportOptions] = None,
) -> Path:
    """Convert a `.kbp` file on disk to an `.ass` subtitle file."""
    opts = options if options is not None else AssExportOptions()
    kbp_file = load_kbp_for_ass(kbp_path)
    return _write_ass_document(kbp_file, output_path, opts)
