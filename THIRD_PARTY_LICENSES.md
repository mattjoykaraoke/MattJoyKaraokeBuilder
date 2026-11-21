# Third-Party Licenses & Attribution

Advanced KBP Editor is **Proprietary Software — All Rights Reserved** (see [`LICENSE`](LICENSE)).

All runtime dependencies used by Advanced KBP Editor are licensed under permissive open-source licenses (MIT / BSD) or **LGPL** (dynamically linked), which permits proprietary distribution with proper attribution and dynamic linking. **No GPL-only or third-party proprietary libraries are bundled or required.**

---

## Runtime Dependencies

### 1. PySide6 / Qt for Python & shiboken6
- **License:** GNU Lesser General Public License v3.0 (LGPL-3.0-only)
- **Website:** https://www.qt.io/qt-for-python
- **Source:** https://code.qt.io/cgit/pyside/pyside-setup.git/
- **Usage:** Desktop GUI framework, audio playback (`QtMultimedia`), and rendering. Dynamically linked via shared libraries (`.dll` / `.so` / `.dylib`).

### 2. FFmpeg (via Qt Multimedia)
- **License:** GNU Lesser General Public License v2.1 or later (LGPL-2.1+)
- **Website:** https://ffmpeg.org/
- **Usage:** Official PySide6 `QtMultimedia` wheels bundle LGPL-configured FFmpeg shared libraries (`avcodec`, `avformat`, `avutil`) without `--enable-gpl` or `--enable-nonfree`.
- **Important Packaging Note:** Advanced KBP Editor does **not** invoke or bundle a standalone `ffmpeg` CLI executable. If future features add external FFmpeg video/audio rendering, users will be prompted to point to their own local FFmpeg installation so that no GPL-licensed FFmpeg binary is ever packaged or redistributed with this application.

### 3. python-soundfile & libsndfile
- **python-soundfile License:** BSD-3-Clause — https://github.com/bastibe/python-soundfile
- **libsndfile License:** GNU Lesser General Public License v2.1 or later (LGPL-2.1+) — https://libsndfile.github.io/libsndfile/
- **Usage:** Fast audio decoding for waveform peak extraction and low-latency audio scrubbing (`libsndfile` is dynamically linked).

### 4. NumPy
- **License:** BSD-3-Clause (with MIT / 0BSD / Zlib / CC0-1.0 components)
- **Website:** https://numpy.org/
- **Usage:** Vectorized waveform peak envelope computation and PCM audio buffer processing.

### 5. cffi & pycparser
- **cffi License:** MIT — https://cffi.readthedocs.io/
- **pycparser License:** BSD-3-Clause — https://github.com/eliben/pycparser
- **Usage:** Foreign function interface used by `soundfile` to load `libsndfile`.

---

## Development / Test Dependencies (Not Distributed in Builds)

- **pytest** — MIT License (https://docs.pytest.org/)

---

## LGPL Compliance Notes for Binary Distribution

When packaging Advanced KBP Editor into a standalone release (e.g., via PyInstaller or Nuitka):
1. Distribute using **dynamic linking / directory mode** (e.g., PyInstaller `--onedir`) so end users can replace the LGPL shared libraries (`Qt6*.dll`, `avcodec*.dll`, `avformat*.dll`, `avutil*.dll`, `libsndfile*.dll`) as required by LGPL-2.1 / LGPL-3.0.
2. Include both `LICENSE` and `THIRD_PARTY_LICENSES.md` in the distributed release folder/installer.
3. Do **not** bundle any GPL-compiled `ffmpeg.exe` binary; keep any external CLI tools user-supplied.
