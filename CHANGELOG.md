# Changelog

All notable changes to **Matt Joy Karaoke Builder** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-11-23

### Added
- **KBP Fixed Text (No Wipe) support:** Lowercase style letters (`a`–`z`) are now recognized and rendered without wiping in the Live Preview, with **Fixed** toggles in the Lyrics Wiping ribbon, Add Line dialog, Display / Remove Window tab, and both sidebar & canvas right-click menus.
- **Blank Spacer Line option in Add Line:** Added a **Blank Spacer Line (vertical gap)** checkbox and **Before / After Selected Line** position selector to the **Add Line** dialog.
- **Universal lead-in glyph detection:** Any leading non-alphanumeric symbol or countdown bar (e.g., `▅▅▅▅▅▅▅`, `➤`, `▶`, `➣➣➣`, `••`) is automatically flagged as a glyph, highlighted in Lyrics Wiping, and supported by **Optimized Windows**.

### Changed
- **Cleaner right-click menus in Lyrics Wiping:** Unified sidebar and main-screen context menus so Line, Word/Syllable, and Page right-click menus each show only relevant actions (including **Delete Line** and **Split Page Here** on Line menus, and **Split Line at This Syllable** on Word menus).
- **Duplicate Line & Duplicate Page:** Now preserve the original line/page style(s) instead of auto-advancing to the next style.

### Fixed
- **Spacebar play/pause:** Fixed pausing during silent playback (when no audio file is loaded) and prevented playback / Fixed toolbar controls from stealing Spacebar focus.
- **Blank spacer lines (`0/0`):** Empty layout-spacer lines (top, middle, or bottom of a page) stay pinned at `0–0` and no longer corrupt page lead-in timings or row-clear checks during **Reset / Optimized Windows**.
- **Fixed-text page handoffs:** Fixed header lines now clear right before the next page's lead-in starts, preventing duplicate overlapping headers and zero-lead-in page pop-ins.
- **Live Preview row alignment:** Switched the 16:9 Live Preview to KBS / `.ass` top-anchored row positioning (honoring per-line `right`/`down` offsets) so cross-page transitions with different line counts no longer shift vertically or overlap.

## [1.0.0] - 2026-11-22

### Added
- Initial release of **Matt Joy Karaoke Builder**.
