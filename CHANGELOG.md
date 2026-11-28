# Changelog

All notable changes to **Matt Joy Karaoke Builder** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.4] - 2026-11-27

### Added
- **16:9 Safe-Area Page Borders & Width Warnings in Tap Sync:**
  - Added vertical 16:9 safe-area guide borders to the **Tap Sync** rolling teleprompter, scaled 1:1 from the active `.kbp` style template font and margins (`1920×1080` logical width matching the Live Preview).
  - Renders the active teleprompter line in the active KBP style font (including uppercase mode) with crisp border ticks and a live width percentage readout that flags lines approaching (`≥90%`, amber) or exceeding (`>100%`, red) the 16:9 border.
  - Highlights near-border and overflowing unsynced lines in the sidebar tree with per-line 16:9 width tooltips (`%` and active font/size), updating automatically when switching `.kbp` style templates.
- **Double-Click Single-Page Lyrics Editor in Tap Sync:**
  - Double-clicking any line in the **Tap Sync** teleprompter or any page/line in the sidebar opens a focused **Edit Page Lyrics** dialog (`Ctrl+Enter` to apply) with live 16:9 border-fit feedback for the widest line on the page.
  - Supports inserting blank lines to split a page into multiple pages (or clearing all text to remove an empty page) while preserving styles, alignment, and existing syllable timestamps on unchanged lines, keeping focus on the edited page.
- **Undo / Redo for Tap Sync Lyric Edits (`Ctrl+Z` / `Ctrl+Y` / `Ctrl+Shift+Z`):**
  - Added Undo/Redo history for Tap Sync lyric edits (both full **Edit Lyrics** and single-page **Edit Page Lyrics**), wired into the **Edit** menu with descriptive action labels.
  - Scoped main-window Undo/Redo shortcuts to `WindowShortcut` so modal text editors retain native `Ctrl+Z` / `Ctrl+Y` text editing.

## [1.1.3] - 2026-11-27

### Added
- **Move Line Up / Down (Within & Across Pages):**
  - Added **Move Line Up (`Alt+Up`)** and **Move Line Down (`Alt+Down`)** in the **Lyrics Wiping** ribbon (**Lines** → `↑` / `↓`), the line right-click menu (sidebar & timeline canvas), and the **Edit** menu so duplicated or existing lines can be shuffled to any slot within a page or across page boundaries (automatically removing any source page left empty).
  - Keeps syllable timestamps unchanged and preserves whole-line selection after each move so you can duplicate a repeating line, shuffle it into its target row or page, and immediately drag it to its singing position.
  - *Note on Auto-Reset Display:* Because shuffling preserves the line's original timestamps, if **Auto-Reset Display** is enabled, the page's display windows may look temporarily out of order until you drag the moved line forward to where it is actually sung — once dragged into place, the display windows recalculate cleanly.

## [1.1.2] - 2026-11-27

### Added
- **GitHub Release Update Checker & In-App Installer:**
  - Automatically checks GitHub Releases in the background on startup (at most once every 24 hours, failing silently when offline) and displays a dismissible top banner when a newer release is available.
  - Supports **Download & Install** directly from the banner (downloading the Windows `.exe` installer with a cancellable progress dialog, prompting to save unsaved work, and launching the installer cleanly), with automatic fallback to opening the GitHub Release page.
  - Includes a **What's New** release notes viewer, **Skip Version** option, and a new **Help** menu (**Check for Updates…**, **Check for Updates Automatically on Startup**, and **About** with a **Check for Updates…** button).

### Changed
- **Instant Low-Latency `1.0x` Audio Playback:**
  - Replaced `QMediaPlayer` for `1.0x` playback with a persistent, pre-warmed `QAudioSink` streaming full-quality in-memory int16 PCM (`25 ms` hardware buffer), enabling sub-millisecond play/pause, instant range previews, seamless main/vocal audio switching, and zero-debounce seeking without WASAPI reopen delays.
  - Upgraded playhead clock tracking to ~120 fps (`8 ms` polling) using hardware-interpolated `processedUSecs()`, and tightened `QMediaPlayer` warm-up and phase-lock slew when playing at slower speeds (`50%` / `75%`).
- **Optimized 120 fps Timeline & Minimap Rendering:**
  - Switched playhead updates on **Lyrics Wiping**, **Display / Remove Window**, and the **Song Minimap** from full-widget repaints to targeted dirty-strip redraws around the old and new playhead positions, skipping sub-pixel no-ops.
  - Clipped **Lyrics Wiping** grid and time-ruler painting to the active repaint region and deduplicated transport time-label text updates.

## [1.1.1] - 2026-11-24

### Added
- **Split-Screen Duet Zones (`🎭 Duet Zones`):**
  - Reserves the **Top Half (Rows 1–3)** for **`[A]` Singer 1** and **Bottom Half (Rows 4–6)** for **`[B]` Singer 2** via native KBP `down` (`offset_y`) coordinates, keeping Singer 2 anchored at Row 4 even on solo pages without inserting blank spacer lines.
  - Places **`[C]` Together** in whichever 3-row zone is free when sharing a page, or across the full 6-row screen when alone.
  - Auto-splits overflowing $>3$-line singer blocks across pages (paired by singing timestamp) while letting overlapping lines sustain uninterrupted across the other zone's page transitions.
  - Added a **`🎭 Duet Zones`** toggle (with `↑` / `↓` zone indicators) and **`Re-Split & Align Zones`** button on the **Display / Remove Window** tab, auto-enabled when building from Tap Sync Duet Mode and auto-detected when reopening saved Duet `.kbp` files.
- **Call-and-Response Duet Window Optimization:**
  - Pulls upcoming response `display_start` times back into unused screen zones so Call & Response lines appear together at the start of an exchange instead of popping in mid-line.
  - Checks row clearing across all prior pages independently per zone and restricts the `25 cs` pre-glyph shrink to true full-song instrumental breaks.

## [1.1.0] - 2026-11-23

### Added
- **Tap Sync Duet Mode (`🎭 Duet Mode`):** Full two-pass duet syncing workflow for concurrent/overlapping singers:
  - **Interactive Duet Setup:** Assign lines to **`[A]` Singer 1** (default, Red/White), **`[B]` Singer 2** (Blue/White), and **`[C]` Sung Together** (Purple/White) by clicking lines directly on the teleprompter, multi-selecting in the sidebar, or pressing `1` / `2` / `3`, then click **`✓ Ready to Sync`**.
  - **Two-Round Rolling Sync:** **Round 1** highlights `[A]` and `[C]` in color while greying out `[B]`; **Round 2** rewinds and highlights `[B]` while greying out `[A]` and `[C]` without overwriting Round 1 timings (including pass-isolated `Back Line`, `Undo Tap`, and pre-roll).
  - **Scrollable Teleprompter & Split-Lane Waveform:** Scroll through greyed-out lines anytime with the mouse wheel (snapping back on the next tap), and view `[A]` (top, red) and `[B]` (bottom, blue) side-by-side on the live lookahead waveform.
  - **Duet `.kbp` / `.ass` Export Palette:** Automatically builds Duet styles with light-tinted unwiped base colors (`#FFCCCC` / `#CCEEFF` / `#EECCFF`) so singers can see upcoming parts before the wipe, paired with vivid Red / Blue / Purple wipes (`#FF3333` / `#3388FF` / `#BB44FF`), while inheriting font, outline, margin, and background settings from any selected `.kbp` template.

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
