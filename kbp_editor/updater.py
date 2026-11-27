from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from . import APP_GITHUB_REPO, __version__

GITHUB_API_LATEST_URL = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = f"MattJoyKaraokeBuilder/{__version__}"
_VERSION_NUM_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    html_url: str
    notes: str
    installer_url: Optional[str] = None
    installer_name: Optional[str] = None
    installer_size: int = 0


def parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse a semantic version string like 'v1.1.2' or '1.2.0-beta.1' into a numeric tuple."""
    s = (version_str or "").strip()
    if s.startswith(("v", "V")):
        s = s[1:]
    # Drop pre-release / build metadata suffix for base numeric comparison
    base = re.split(r"[-+]", s, maxsplit=1)[0]
    parts = [int(p) for p in base.split(".") if p.isdigit()]
    if parts:
        return tuple(parts)
    nums = _VERSION_NUM_RE.findall(s)
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer_version(latest: str, current: str = __version__) -> bool:
    """Return True if `latest` represents a strictly newer version than `current`."""
    a = parse_version(latest)
    b = parse_version(current)
    max_len = max(len(a), len(b))
    a_pad = a + (0,) * (max_len - len(a))
    b_pad = b + (0,) * (max_len - len(b))
    return a_pad > b_pad


def is_trusted_github_url(url: str) -> bool:
    """Verify that a download or release URL uses HTTPS on an official GitHub host."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return (
        host == "github.com"
        or host.endswith(".github.com")
        or host == "githubusercontent.com"
        or host.endswith(".githubusercontent.com")
    )


def parse_release_payload(payload: Dict[str, Any]) -> Optional[ReleaseInfo]:
    """Parse a GitHub `/releases/latest` JSON payload into a ReleaseInfo object."""
    if not isinstance(payload, dict):
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        return None
    version = tag_name[1:] if tag_name.startswith(("v", "V")) else tag_name

    html_url = str(payload.get("html_url") or "").strip()
    if not is_trusted_github_url(html_url):
        return None

    name = str(payload.get("name") or tag_name).strip()
    notes = str(payload.get("body") or "").strip()

    installer_url: Optional[str] = None
    installer_name: Optional[str] = None
    installer_size = 0

    assets = payload.get("assets") or []
    if isinstance(assets, list):
        candidates = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            a_name = str(asset.get("name") or "").strip()
            a_url = str(asset.get("browser_download_url") or "").strip()
            if not a_name.lower().endswith(".exe"):
                continue
            if not is_trusted_github_url(a_url):
                continue
            a_size = int(asset.get("size") or 0)
            # Prefer the official Inno Setup installer filename
            score = 0
            low = a_name.lower()
            if "setup" in low:
                score += 2
            if "mattjoykaraokebuilder" in low:
                score += 2
            candidates.append((score, a_name, a_url, a_size))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, installer_name, installer_url, installer_size = candidates[0]

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        name=name,
        html_url=html_url,
        notes=notes,
        installer_url=installer_url,
        installer_name=installer_name,
        installer_size=installer_size,
    )


def fetch_latest_release(repo: str = APP_GITHUB_REPO, timeout: float = 6.0) -> Optional[ReleaseInfo]:
    """Query GitHub's public `/releases/latest` endpoint. Returns None if no release exists yet."""
    url = GITHUB_API_LATEST_URL.format(repo=repo)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No published GitHub Release exists yet
            return None
        raise
    return parse_release_payload(data)


def can_run_windows_installer() -> bool:
    """Return True on Windows where launching an Inno Setup .exe installer is supported."""
    return sys.platform == "win32"


def launch_installer(installer_path: str | Path) -> None:
    """Launch the downloaded Inno Setup installer, supporting Windows UAC elevation."""
    p = Path(installer_path)
    if not p.is_file():
        raise FileNotFoundError(f"Installer not found: {p}")
    if sys.platform == "win32":
        # os.startfile uses ShellExecuteW, which handles UAC elevation cleanly
        os.startfile(str(p))
    else:
        subprocess.Popen([str(p)])


class UpdateCheckWorker(QThread):
    """Background worker that checks GitHub for a newer release without blocking the UI."""

    update_available = Signal(object)  # ReleaseInfo
    up_to_date = Signal(str)  # current version string
    check_failed = Signal(str)  # human-readable error message

    def __init__(
        self,
        repo: str = APP_GITHUB_REPO,
        current_version: str = __version__,
        timeout: float = 6.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._repo = repo
        self._current_version = current_version
        self._timeout = timeout

    def run(self) -> None:
        try:
            info = fetch_latest_release(repo=self._repo, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                self.check_failed.emit("GitHub API rate limit reached. Please try again later.")
            else:
                self.check_failed.emit(f"GitHub returned HTTP {exc.code}.")
            return
        except urllib.error.URLError as exc:
            self.check_failed.emit(f"Could not reach GitHub: {exc.reason}")
            return
        except Exception as exc:
            self.check_failed.emit(f"Update check failed: {exc}")
            return

        if info is not None and is_newer_version(info.version, self._current_version):
            self.update_available.emit(info)
        else:
            self.up_to_date.emit(self._current_version)


class InstallerDownloadWorker(QThread):
    """Background worker that downloads a release installer .exe into a temp directory."""

    progress = Signal(int, int)  # downloaded_bytes, total_bytes (0 if unknown)
    finished_ok = Signal(str)  # path to downloaded installer
    failed = Signal(str)  # error message (empty string if cancelled)

    def __init__(self, release: ReleaseInfo, timeout: float = 15.0, parent=None) -> None:
        super().__init__(parent)
        self._release = release
        self._timeout = timeout

    def run(self) -> None:
        url = self._release.installer_url
        if not url or not is_trusted_github_url(url):
            self.failed.emit("No trusted Windows installer asset was found for this release.")
            return

        raw_name = self._release.installer_name or f"MattJoyKaraokeBuilder_Setup_v{self._release.version}.exe"
        safe_name = Path(raw_name).name
        if not safe_name.lower().endswith(".exe"):
            safe_name += ".exe"

        dest_dir = Path(tempfile.gettempdir()) / "MattJoyKaraokeBuilder_Updates"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.failed.emit(f"Could not prepare temporary download folder: {exc}")
            return

        dest_path = dest_dir / safe_name
        part_path = dest_dir / (safe_name + ".part")

        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                final_url = resp.geturl()
                if not is_trusted_github_url(final_url):
                    self.failed.emit("Download redirected to an untrusted host.")
                    return

                content_len = resp.headers.get("Content-Length")
                total_bytes = int(content_len) if content_len and content_len.isdigit() else self._release.installer_size
                downloaded = 0
                self.progress.emit(0, total_bytes)

                with open(part_path, "wb") as fh:
                    while True:
                        if self.isInterruptionRequested():
                            fh.close()
                            try:
                                part_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            self.failed.emit("")
                            return
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total_bytes)

            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            part_path.replace(dest_path)
            self.finished_ok.emit(str(dest_path))
        except Exception as exc:
            try:
                part_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.failed.emit(f"Download failed: {exc}")
