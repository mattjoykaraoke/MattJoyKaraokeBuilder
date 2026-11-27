from __future__ import annotations

from kbp_editor.updater import (
    is_newer_version,
    is_trusted_github_url,
    parse_release_payload,
    parse_version,
)


def test_parse_version_variants() -> None:
    assert parse_version("1.1.2") == (1, 1, 2)
    assert parse_version("v1.1.2") == (1, 1, 2)
    assert parse_version("V2.0.10") == (2, 0, 10)
    assert parse_version("v1.2.3-rc1") == (1, 2, 3)
    assert parse_version("1.2") == (1, 2)
    assert parse_version("") == (0,)


def test_is_newer_version() -> None:
    assert is_newer_version("1.1.3", "1.1.2") is True
    assert is_newer_version("v1.2.0", "1.1.9") is True
    assert is_newer_version("2.0", "1.9.9") is True
    assert is_newer_version("1.1.2", "1.1.2") is False
    assert is_newer_version("v1.1.2", "1.1.2") is False
    assert is_newer_version("1.1.1", "1.1.2") is False
    assert is_newer_version("1.1", "1.1.0") is False


def test_is_trusted_github_url() -> None:
    assert is_trusted_github_url("https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/tag/v1.1.3")
    assert is_trusted_github_url("https://objects.githubusercontent.com/github-production-release-asset/foo.exe")
    assert is_trusted_github_url("https://release-assets.githubusercontent.com/abc/setup.exe")
    assert not is_trusted_github_url("http://github.com/mattjoykaraoke/MattJoyKaraokeBuilder")
    assert not is_trusted_github_url("https://evilgithub.com/setup.exe")
    assert not is_trusted_github_url("https://example.com/setup.exe")
    assert not is_trusted_github_url("")


def test_parse_release_payload_picks_setup_installer() -> None:
    payload = {
        "tag_name": "v1.1.3",
        "name": "v1.1.3 — Update Checker",
        "html_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/tag/v1.1.3",
        "body": "### Added\n- Auto-update banner",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "source.zip",
                "browser_download_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/download/v1.1.3/source.zip",
                "size": 1000,
            },
            {
                "name": "helper.exe",
                "browser_download_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/download/v1.1.3/helper.exe",
                "size": 2000,
            },
            {
                "name": "MattJoyKaraokeBuilder_Setup_v1.1.3.exe",
                "browser_download_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/download/v1.1.3/MattJoyKaraokeBuilder_Setup_v1.1.3.exe",
                "size": 45_000_000,
            },
        ],
    }
    info = parse_release_payload(payload)
    assert info is not None
    assert info.version == "1.1.3"
    assert info.tag_name == "v1.1.3"
    assert info.installer_name == "MattJoyKaraokeBuilder_Setup_v1.1.3.exe"
    assert info.installer_url is not None and info.installer_url.endswith("MattJoyKaraokeBuilder_Setup_v1.1.3.exe")
    assert info.installer_size == 45_000_000


def test_parse_release_payload_fallback_without_exe() -> None:
    payload = {
        "tag_name": "v1.1.3",
        "name": "v1.1.3",
        "html_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/tag/v1.1.3",
        "body": "Notes",
        "draft": False,
        "prerelease": False,
        "assets": [],
    }
    info = parse_release_payload(payload)
    assert info is not None
    assert info.installer_url is None
    assert info.installer_name is None


def test_parse_release_payload_ignores_draft_prerelease_and_untrusted_assets() -> None:
    base = {
        "tag_name": "v1.2.0",
        "html_url": "https://github.com/mattjoykaraoke/MattJoyKaraokeBuilder/releases/tag/v1.2.0",
        "assets": [
            {
                "name": "MattJoyKaraokeBuilder_Setup_v1.2.0.exe",
                "browser_download_url": "https://evil.example.com/setup.exe",
                "size": 123,
            }
        ],
    }
    assert parse_release_payload({**base, "draft": True}) is None
    assert parse_release_payload({**base, "prerelease": True}) is None

    info = parse_release_payload({**base, "draft": False, "prerelease": False})
    assert info is not None
    assert info.installer_url is None
