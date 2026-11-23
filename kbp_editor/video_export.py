from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import soundfile as sf


FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/"

_NVENC_CACHE: Dict[str, bool] = {}


def default_ffmpeg_candidates() -> List[Path]:
    """Return common Windows install locations for ffmpeg.exe."""
    candidates: List[Path] = []
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))
    candidates.extend([
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"),
    ])
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
    return candidates


def find_ffmpeg_exe(
    configured_path: str = "",
    candidates: Optional[Sequence[Path]] = None,
) -> Optional[Path]:
    """
    Locate ffmpeg.exe, checking any saved user path (file or bin directory) first,
    then PATH and common Windows install folders. Returns None if not found.
    """
    if configured_path:
        p = Path(configured_path)
        if p.is_file():
            return p
        if p.is_dir():
            cand = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if cand.is_file():
                return cand

    search_list = candidates if candidates is not None else default_ffmpeg_candidates()
    for cand in search_list:
        if cand.is_file():
            return cand
    return None


def find_ffprobe_exe(ffmpeg_exe: str | Path) -> str:
    """Locate ffprobe alongside ffmpeg_exe, or fall back to PATH."""
    ffp = Path(ffmpeg_exe)
    sibling = ffp.parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    which = shutil.which("ffprobe")
    return which or "ffprobe"


def has_nvenc_support(ffmpeg_exe: str | Path) -> bool:
    """Return True if the given ffmpeg binary supports h264_nvenc encoding (cached)."""
    key = str(ffmpeg_exe)
    if key in _NVENC_CACHE:
        return _NVENC_CACHE[key]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        res = subprocess.run(
            [key, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=10,
            check=False,
        )
        supported = res.returncode == 0 and "h264_nvenc" in (res.stdout or "")
    except Exception:
        supported = False
    _NVENC_CACHE[key] = supported
    return supported


def get_audio_duration(audio_path: str | Path, ffprobe_exe: str = "ffprobe") -> float:
    """Return audio duration in seconds using soundfile first, falling back to ffprobe."""
    p = Path(audio_path)
    if not p.is_file():
        return 0.0
    try:
        info = sf.info(str(p))
        if info.duration > 0:
            return float(info.duration)
    except Exception:
        pass
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.check_output(
            [
                ffprobe_exe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=15,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def parse_ass_background_color(ass_path: str | Path) -> Optional[str]:
    """Parse an .ass file for the `kbputils_background_1.0 color:` comment and return `#RRGGBB`."""
    try:
        with Path(ass_path).open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                if "kbputils_background_1.0 color:" in line.lower():
                    match = re.search(r"#([0-9a-fA-F]+)", line)
                    if match:
                        hex_val = match.group(1)
                        if len(hex_val) == 8:
                            return "#" + hex_val[2:]
                        if len(hex_val) == 6:
                            return "#" + hex_val
                    break
    except Exception:
        pass
    return None


def check_ass_should_be_transparent(ass_path: str | Path) -> bool:
    """
    Return True if the .ass background comment has alpha != FF, or if any Style/Dialogue
    uses non-opaque transparency in Primary/Secondary/Outline colors (ignoring shadow alpha).
    """
    p = Path(ass_path)
    if not p.is_file():
        return False
    try:
        format_indices = {
            "primarycolour": 3,
            "secondarycolour": 4,
            "outlinecolour": 5,
        }
        with p.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                line_lower = line.strip().lower()

                if "kbputils_background_1.0 color:" in line_lower:
                    match = re.search(r"#([0-9a-fA-F]+)", line)
                    if match:
                        hex_val = match.group(1)
                        if len(hex_val) == 8 and hex_val[:2].upper() != "FF":
                            return True
                    continue

                if line_lower.startswith("format:"):
                    parts = [part.strip() for part in line_lower[7:].split(",")]
                    for key in ("primarycolour", "secondarycolour", "outlinecolour"):
                        if key in parts:
                            format_indices[key] = parts.index(key)
                    continue

                if line_lower.startswith("style:"):
                    parts = [part.strip() for part in line[6:].split(",")]
                    for key in ("primarycolour", "secondarycolour", "outlinecolour"):
                        idx = format_indices.get(key)
                        if idx is not None and idx < len(parts):
                            color_str = parts[idx]
                            if color_str.startswith("&H") or color_str.startswith("&h"):
                                hex_part = color_str[2:].rstrip("&")
                                if len(hex_part) == 8 and hex_part[:2].upper() != "00":
                                    return True
                            else:
                                try:
                                    val = int(color_str)
                                    hex_part = f"{val & 0xFFFFFFFF:08X}"
                                    if hex_part[:2] != "00":
                                        return True
                                except ValueError:
                                    pass
                    continue

                if line_lower.startswith("dialogue:"):
                    for aa in re.findall(r"\\[1-3]a&h([0-9a-f]{2})&", line_lower):
                        if aa != "00":
                            return True
                    for aa in re.findall(r"\\alpha&h([0-9a-f]{2})&", line_lower):
                        if aa != "00":
                            return True
    except Exception:
        pass
    return False


def build_ffmpeg_video_command(
    ffmpeg_exe: str | Path,
    audio_path: str | Path,
    ass_path: str | Path,
    output_path: str | Path,
    bg_color: str,
    duration: float,
    *,
    transparent: bool = False,
    use_nvenc: Optional[bool] = None,
) -> List[str]:
    """Construct the exact MJKCS FFmpeg command list for .mp4 or transparent ProRes .mov rendering."""
    exe = str(ffmpeg_exe)
    audio = str(audio_path)
    ass_filename = Path(ass_path).name
    out = str(output_path)

    if transparent:
        if out.lower().endswith(".mp4"):
            out = str(Path(out).with_suffix(".mov"))
        filter_complex = (
            f"color=color=000000@0.0:d={duration}:r=60:s=1920x1080,"
            f"format=rgba[s0];[s0]ass={ass_filename}:alpha=1[s1]"
        )
        return [
            exe,
            "-i",
            audio,
            "-filter_complex",
            filter_complex,
            "-map",
            "[s1]",
            "-map",
            "0:a",
            "-c:v",
            "prores",
            "-pix_fmt",
            "yuva444p10le",
            "-c:a",
            "pcm_s24le",
            "-y",
            out,
        ]

    bg_hex = (bg_color or "#000000").lstrip("#") or "000000"
    filter_complex = f"color=color={bg_hex}:d={duration}:r=60:s=1920x1080[s0];[s0]ass={ass_filename}[s1]"
    if use_nvenc is None:
        use_nvenc = has_nvenc_support(exe)

    if use_nvenc:
        video_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    else:
        video_args = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "23"]

    return [
        exe,
        "-i",
        audio,
        "-filter_complex",
        filter_complex,
        "-map",
        "[s1]",
        "-map",
        "0:a",
        "-f",
        "mp4",
        "-c:a",
        "copy",
        *video_args,
        "-hide_banner",
        "-pix_fmt",
        "yuv420p",
        "-y",
        out,
    ]


def render_karaoke_video(
    ffmpeg_exe: str | Path,
    ass_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    bg_color: str = "#000000",
    *,
    transparent: bool = False,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    process_holder: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Render a karaoke video (.mp4 or transparent .mov) using FFmpeg.
    Copies the .ass to a safe temporary filename in cwd so FFmpeg's ass filter
    never fails on special path characters, writes to a temporary `.rendering`
    file, falls back from NVENC to CPU libx264 if needed, and atomically replaces
    the final output path on success.
    """
    exe = Path(ffmpeg_exe)
    if not exe.is_file():
        raise FileNotFoundError(f"FFmpeg executable not found:\n{exe}")
    ass = Path(ass_path)
    if not ass.is_file():
        raise FileNotFoundError(f"Subtitle file not found:\n{ass}")
    audio = Path(audio_path)
    if not audio.is_file():
        raise FileNotFoundError(f"Instrumental audio file not found:\n{audio}")

    out = Path(output_path)
    if transparent and out.suffix.lower() == ".mp4":
        out = out.with_suffix(".mov")
    elif (not transparent) and not out.suffix:
        out = out.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    ffprobe_exe = find_ffprobe_exe(exe)
    duration = get_audio_duration(audio, ffprobe_exe=ffprobe_exe)
    if duration <= 0:
        raise RuntimeError("Could not determine audio duration. Check that the audio file and FFmpeg/FFprobe are valid.")

    work_dir = ass.parent
    safe_ass_path = work_dir / "temp_render_subs.ass"
    shutil.copy2(ass, safe_ass_path)

    ext = out.suffix or (".mov" if transparent else ".mp4")
    temp_output_path = out.with_name(f"{out.stem}.rendering{ext}")
    if temp_output_path.exists():
        temp_output_path.unlink()

    use_nvenc = (not transparent) and has_nvenc_support(exe)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def _run_pass(nvenc_enabled: bool) -> tuple[int, List[str]]:
        cmd = build_ffmpeg_video_command(
            exe,
            audio,
            safe_ass_path,
            temp_output_path,
            bg_color,
            duration,
            transparent=transparent,
            use_nvenc=nvenc_enabled,
        )
        encoder_label = "NVIDIA NVENC" if nvenc_enabled else ("ProRes Alpha" if transparent else "libx264")
        if progress_cb is not None:
            progress_cb(f"Rendering with FFmpeg ({encoder_label})…")

        proc = subprocess.Popen(
            cmd,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        if process_holder is not None:
            process_holder["proc"] = proc

        tail: List[str] = []
        try:
            if proc.stdout is not None:
                for raw_line in proc.stdout:
                    for part in re.split(r"[\r\n]+", raw_line):
                        cleaned = part.strip()
                        if not cleaned:
                            continue
                        tail.append(cleaned)
                        if len(tail) > 80:
                            tail = tail[-80:]
                        if progress_cb is not None:
                            progress_cb(cleaned)
            proc.wait()
            return proc.returncode, tail
        finally:
            if process_holder is not None:
                process_holder.pop("proc", None)

    output_lines: List[str] = []
    try:
        rc, output_lines = _run_pass(use_nvenc)
        cancelled = bool(cancel_event and cancel_event.is_set())
        if (not cancelled) and use_nvenc and rc != 0:
            if progress_cb is not None:
                progress_cb("NVENC render failed; retrying with CPU libx264…")
            if temp_output_path.exists():
                temp_output_path.unlink()
            rc, output_lines = _run_pass(False)
    finally:
        try:
            if safe_ass_path.exists():
                safe_ass_path.unlink()
        except Exception:
            pass

    if cancel_event and cancel_event.is_set():
        try:
            if temp_output_path.exists():
                temp_output_path.unlink()
        except Exception:
            pass
        raise RuntimeError("Video generation was cancelled.")

    if rc == 0 and temp_output_path.is_file() and temp_output_path.stat().st_size > 0:
        os.replace(temp_output_path, out)
        return out

    try:
        if temp_output_path.exists():
            temp_output_path.unlink()
    except Exception:
        pass

    ffmpeg_tail = "\n".join(output_lines).strip()
    if len(ffmpeg_tail) > 1600:
        ffmpeg_tail = "…" + ffmpeg_tail[-1600:]
    detail = f"\n\nFFmpeg output:\n{ffmpeg_tail}" if ffmpeg_tail else ""
    raise RuntimeError(f"FFmpeg video generation failed (exit code {rc}).{detail}")


@dataclass(frozen=True)
class VideoStreamInfo:
    """Probed video/audio stream parameters used to match a title card clip for lossless concat."""

    width: int = 1920
    height: int = 1080
    fps: str = "60"
    vcodec: str = "h264"
    pix_fmt: str = "yuv420p"
    video_timescale: Optional[int] = None
    acodec: str = "aac"
    sample_rate: int = 44100
    channels: int = 2
    channel_layout: str = "stereo"
    has_audio: bool = True


_AUDIO_ENCODER_MAP: Dict[str, str] = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "flac": "flac",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
    "pcm_s32le": "pcm_s32le",
    "pcm_f32le": "pcm_f32le",
    "alac": "alac",
}


def probe_video_streams(ffprobe_exe: str, video_path: str | Path) -> VideoStreamInfo:
    """Inspect a rendered video with ffprobe so a prepended title card matches its streams exactly."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        raw = subprocess.check_output(
            [
                ffprobe_exe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels,channel_layout,time_base",
                "-of",
                "json",
                str(video_path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=15,
        )
        data = json.loads(raw)
        streams = data.get("streams", []) if isinstance(data, dict) else []
    except Exception:
        return VideoStreamInfo()

    width, height = 1920, 1080
    fps = "60"
    vcodec = "h264"
    pix_fmt = "yuv420p"
    video_timescale: Optional[int] = None
    acodec = "aac"
    sample_rate = 44100
    channels = 2
    channel_layout = "stereo"
    has_audio = False

    for st in streams:
        ctype = st.get("codec_type")
        if ctype == "video":
            width = int(st.get("width") or width)
            height = int(st.get("height") or height)
            vcodec = str(st.get("codec_name") or vcodec)
            pix_fmt = str(st.get("pix_fmt") or pix_fmt)
            rfr = str(st.get("r_frame_rate") or "")
            if rfr and rfr != "0/0":
                fps = rfr
            tb = str(st.get("time_base") or "")
            if "/" in tb:
                try:
                    num, den = tb.split("/", 1)
                    if int(num) == 1 and int(den) > 0:
                        video_timescale = int(den)
                except ValueError:
                    pass
        elif ctype == "audio" and not has_audio:
            has_audio = True
            acodec = str(st.get("codec_name") or acodec)
            try:
                sample_rate = int(st.get("sample_rate") or sample_rate)
            except (TypeError, ValueError):
                pass
            try:
                channels = int(st.get("channels") or channels)
            except (TypeError, ValueError):
                pass
            cl = str(st.get("channel_layout") or "").strip()
            channel_layout = cl if cl else ("mono" if channels == 1 else "stereo")

    return VideoStreamInfo(
        width=width,
        height=height,
        fps=fps,
        vcodec=vcodec,
        pix_fmt=pix_fmt,
        video_timescale=video_timescale,
        acodec=acodec,
        sample_rate=sample_rate,
        channels=channels,
        channel_layout=channel_layout,
        has_audio=has_audio,
    )


def build_title_card_clip_command(
    ffmpeg_exe: str | Path,
    image_path: str | Path,
    duration_sec: float,
    output_clip_path: str | Path,
    stream_info: VideoStreamInfo,
) -> List[str]:
    """
    Build the FFmpeg command to encode a short static title-card clip matching `stream_info`
    so it can be stream-copy concatenated (`-c copy`) before the karaoke video.
    """
    dur = max(0.2, float(duration_sec))
    w, h = stream_info.width, stream_info.height
    is_prores = stream_info.vcodec.lower().startswith("prores") or str(output_clip_path).lower().endswith(".mov")
    pad_color = "0x00000000" if ("a" in stream_info.pix_fmt or is_prores) else "black"
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={pad_color},"
        f"setsar=1,fps={stream_info.fps}"
    )

    cmd: List[str] = [
        str(ffmpeg_exe),
        "-hide_banner",
        "-loop",
        "1",
        "-framerate",
        stream_info.fps,
        "-t",
        f"{dur:.3f}",
        "-i",
        str(image_path),
    ]

    if stream_info.has_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-t",
            f"{dur:.3f}",
            "-i",
            f"anullsrc=channel_layout={stream_info.channel_layout}:sample_rate={stream_info.sample_rate}",
        ])

    cmd.extend(["-vf", vf])

    if is_prores:
        cmd.extend(["-c:v", "prores", "-pix_fmt", stream_info.pix_fmt or "yuva444p10le"])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "23", "-pix_fmt", stream_info.pix_fmt or "yuv420p"])
        if stream_info.video_timescale:
            cmd.extend(["-video_track_timescale", str(stream_info.video_timescale)])

    if stream_info.has_audio:
        a_enc = _AUDIO_ENCODER_MAP.get(stream_info.acodec.lower(), "aac")
        cmd.extend(["-c:a", a_enc, "-ar", str(stream_info.sample_rate), "-ac", str(stream_info.channels)])
        if a_enc in ("aac", "libmp3lame", "libopus"):
            cmd.extend(["-b:a", "192k"])
        cmd.append("-shortest")
    else:
        cmd.append("-an")

    cmd.extend(["-y", str(output_clip_path)])
    return cmd


def prepend_title_card(
    ffmpeg_exe: str | Path,
    video_path: str | Path,
    image_path: str | Path,
    output_path: str | Path,
    duration_sec: float = 3.0,
    *,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Path:
    """
    Prepend a static title card image (shown for `duration_sec` seconds) before `video_path`
    by encoding only the short title card clip and stream-copy concatenating (`-c copy`)
    without re-encoding the karaoke video.
    """
    exe = Path(ffmpeg_exe)
    if not exe.is_file():
        raise FileNotFoundError(f"FFmpeg executable not found:\n{exe}")
    vid = Path(video_path)
    if not vid.is_file():
        raise FileNotFoundError(f"Video file not found:\n{vid}")
    img = Path(image_path)
    if not img.is_file():
        raise FileNotFoundError(f"Title card image not found:\n{img}")

    out = Path(output_path)
    ext = vid.suffix or ".mp4"
    if not out.suffix:
        out = out.with_suffix(ext)
    out.parent.mkdir(parents=True, exist_ok=True)

    ffprobe_exe = find_ffprobe_exe(exe)
    if progress_cb is not None:
        progress_cb("Probing video streams…")
    info = probe_video_streams(ffprobe_exe, vid)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    with tempfile.TemporaryDirectory(prefix="kbp_titlecard_") as tmp_dir:
        tmp = Path(tmp_dir)
        intro_clip = tmp / f"title_card{ext}"
        list_file = tmp / "concat_list.txt"
        temp_out = out.with_name(f"{out.stem}.concat{ext}")
        if temp_out.exists():
            temp_out.unlink()

        if progress_cb is not None:
            progress_cb(f"Encoding {duration_sec:.1f}s title card clip…")
        clip_cmd = build_title_card_clip_command(exe, img, duration_sec, intro_clip, info)
        r1 = subprocess.run(
            clip_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=120,
            check=False,
        )
        if r1.returncode != 0 or not intro_clip.is_file():
            detail = (r1.stderr or r1.stdout or "").strip()
            raise RuntimeError(f"Failed to encode title card clip (exit code {r1.returncode}).\n{detail}")

        def _ff_quote(p: Path) -> str:
            return str(p.resolve()).replace("\\", "/").replace("'", "'\\''")

        list_file.write_text(
            f"file '{_ff_quote(intro_clip)}'\nfile '{_ff_quote(vid)}'\n",
            encoding="utf-8",
        )

        if progress_cb is not None:
            progress_cb("Joining title card and video (stream copy)…")
        concat_cmd = [
            str(exe),
            "-hide_banner",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-y",
            str(temp_out),
        ]
        r2 = subprocess.run(
            concat_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=180,
            check=False,
        )
        if r2.returncode != 0 or not temp_out.is_file() or temp_out.stat().st_size == 0:
            try:
                if temp_out.exists():
                    temp_out.unlink()
            except Exception:
                pass
            detail = (r2.stderr or r2.stdout or "").strip()
            raise RuntimeError(f"Failed to join title card with video (exit code {r2.returncode}).\n{detail}")

        os.replace(temp_out, out)
        return out
