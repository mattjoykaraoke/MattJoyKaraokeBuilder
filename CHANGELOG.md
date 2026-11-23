# Changelog

All notable changes to **Matt Joy Karaoke Builder** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-11-23

### Added
- **KBP Fixed Text (No Wipe) support:** Lowercase style letters (`a`–`z`) are now recognized and rendered without wiping in the Live Preview, with **Fixed** toggles in the Lyrics Wiping ribbon, Add Line dialog, Display / Remove Window tab, and both sidebar & canvas right-click menus.
- **Universal lead-in glyph detection:** Any leading non-alphanumeric symbol or countdown bar (e.g., `▅▅▅▅▅▅▅`, `➤`, `▶`, `➣➣➣`, `••`) is automatically flagged as a glyph, highlighted in Lyrics Wiping, and supported by **Optimized Windows**.

### Fixed
- **Blank spacer lines (`0/0`):** Empty layout-spacer lines (top, middle, or bottom of a page) stay pinned at `0–0` and no longer corrupt page lead-in timings or row-clear checks during **Reset / Optimized Windows**.
- **Fixed-text page handoffs:** Fixed header lines now clear right before the next page's lead-in starts, preventing duplicate overlapping headers and zero-lead-in page pop-ins.
- **Live Preview row alignment:** Switched the 16:9 Live Preview to KBS / `.ass` top-anchored row positioning (honoring per-line `right`/`down` offsets) so cross-page transitions with different line counts no longer shift vertically or overlap.

## [1.0.0] - 2026-11-22

### Added
- Initial release of **Matt Joy Karaoke Builder**.
