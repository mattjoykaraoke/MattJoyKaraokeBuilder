from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


CHROMALYRIC_DOWNLOAD_URL = "https://github.com/mattjoykaraoke/ChromaLyric"


@dataclass(frozen=True)
class ChromaLyricCliOptions:
    """Optional ChromaLyric CLI styling/geometry flags applied before opening the .ass file."""

    theme_mode: str = "none"  # "none", "preset", "extract", "randomize"
    preset_name: str = ""
    extract_image_path: str = ""
    auto_safe_size: bool = False
    pseudo_3d: bool = False
    shadow_layers: Optional[int] = None

    def to_cli_args(self) -> List[str]:
        """Return CLI flags for any modified options, or an empty list if all are default."""
        args: List[str] = []
        if self.theme_mode == "preset" and self.preset_name.strip():
            args.extend(["--apply-preset", self.preset_name.strip()])
        elif self.theme_mode == "extract" and self.extract_image_path.strip():
            args.extend(["--extract-theme", self.extract_image_path.strip(), "--gui"])
        elif self.theme_mode == "randomize":
            args.append("--randomize")

        if self.auto_safe_size:
            args.append("--auto-safe-size")
        if self.pseudo_3d:
            args.extend(["--pseudo-3d", "true"])
        if self.shadow_layers is not None:
            layers = max(1, min(15, int(self.shadow_layers)))
            args.extend(["--shadow-layers", str(layers)])
        return args

    @property
    def has_modifications(self) -> bool:
        return bool(self.to_cli_args())


def default_chromalyric_candidates() -> List[Path]:
    """Return standard Windows install locations for ChromaLyric."""
    candidates: List[Path] = [
        Path(r"C:\Program Files\ChromaLyric\ChromaLyric.exe"),
        Path(r"C:\Program Files (x86)\ChromaLyric\ChromaLyric.exe"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "ChromaLyric" / "ChromaLyric.exe")
        candidates.append(Path(local_app_data) / "ChromaLyric" / "ChromaLyric.exe")
    return candidates


def find_chromalyric_exe(
    configured_path: str = "",
    candidates: Optional[Sequence[Path]] = None,
) -> Optional[Path]:
    """
    Locate ChromaLyric.exe, checking any saved user path first,
    then standard install paths. Returns None if not found.
    """
    if configured_path:
        p = Path(configured_path)
        if p.is_file():
            return p

    search_list = candidates if candidates is not None else default_chromalyric_candidates()
    for cand in search_list:
        if cand.is_file():
            return cand
    return None


def build_chromalyric_command(
    exe_path: str | Path,
    ass_path: str | Path,
    options: Optional[ChromaLyricCliOptions] = None,
) -> Tuple[List[str], bool]:
    """
    Build the ChromaLyric invocation command for `ass_path`.
    Returns `(cmd, run_headless_first)`:
    - When `options` is None or has no modifications, returns `[exe, ass]` with `run_headless_first=False`.
    - When CLI options are modified and include `--gui` (e.g. `--extract-theme`), returns the
      `--cli --out <ass> ... --gui` command with `run_headless_first=False` (launched directly).
    - Otherwise returns the `--cli --out <ass> ...` command with `run_headless_first=True` so
      the headless styling step completes before opening the styled `.ass` in the ChromaLyric GUI.
    """
    exe = str(exe_path)
    ass = str(ass_path)
    cli_args = options.to_cli_args() if options is not None else []
    if not cli_args:
        return ([exe, ass], False)

    cmd = [exe, ass, "--cli", "--out", ass, *cli_args]
    run_headless_first = "--gui" not in cli_args
    return (cmd, run_headless_first)


def launch_chromalyric(
    exe_path: str | Path,
    ass_path: str | Path,
    options: Optional[ChromaLyricCliOptions] = None,
    *,
    timeout_sec: float = 120.0,
) -> str:
    """
    Open `ass_path` in ChromaLyric, optionally running ChromaLyric CLI flags first when
    `options` contains modified settings. Returns a concise status summary.
    """
    exe = Path(exe_path)
    if not exe.is_file():
        raise FileNotFoundError(f"ChromaLyric executable not found:\n{exe}")

    ass = Path(ass_path)
    if not ass.is_file():
        raise FileNotFoundError(f"ASS subtitle file not found:\n{ass}")

    cmd, run_headless_first = build_chromalyric_command(exe, ass, options)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cwd = str(exe.parent)

    if run_headless_first:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                creationflags=creationflags,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ChromaLyric CLI timed out after {int(timeout_sec)} seconds.") from None

        if result.returncode != 0:
            detail = "\n".join(
                part.strip() for part in (result.stderr, result.stdout) if part and part.strip()
            ).strip()
            if detail:
                raise RuntimeError(f"ChromaLyric CLI failed (exit code {result.returncode}):\n{detail}")
            raise RuntimeError(f"ChromaLyric CLI failed (exit code {result.returncode}).")

        subprocess.Popen([str(exe), str(ass)], cwd=cwd)
        return f"Styled and opened {ass.name} in ChromaLyric"

    if "--cli" in cmd:
        subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags)
        return f"Opened {ass.name} in ChromaLyric with CLI options"

    subprocess.Popen(cmd, cwd=cwd)
    return f"Opened {ass.name} in ChromaLyric"
