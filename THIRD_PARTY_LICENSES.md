# Third-Party Licenses & Attribution

Matt Joy Karaoke Builder is **Proprietary Software — All Rights Reserved** (see [`LICENSE`](LICENSE)).

All runtime dependencies used by Matt Joy Karaoke Builder are licensed under permissive open-source licenses (MIT / BSD) or **LGPL** (dynamically linked), which permits proprietary distribution with proper attribution and dynamic linking. **No GPL-only or third-party proprietary libraries are bundled or required.**

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
- **Important Packaging Note:** Matt Joy Karaoke Builder does **not** invoke or bundle a standalone `ffmpeg` CLI executable. If future features add external FFmpeg video/audio rendering, users will be prompted to point to their own local FFmpeg installation so that no GPL-licensed FFmpeg binary is ever packaged or redistributed with this application.

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

### 6. kbputils (ItMightBeKaraoke)
- **License:** BSD 2-Clause License (`Copyright (c) 2024, ItMightBeKaraoke`)
- **Website:** https://pypi.org/project/kbputils/
- **Usage:** Standalone `.kbp` to `.ass` (Advanced SubStation Alpha) karaoke subtitle conversion in the Finishing tab.

```text
BSD 2-Clause License

Copyright (c) 2024, ItMightBeKaraoke

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### 7. ass_imbk & charset-normalizer (Dependencies of kbputils)
- **ass_imbk License:** MIT License — http://github.com/itmightbekaraoke/python-ass
- **charset-normalizer License:** MIT License — https://github.com/jawah/charset_normalizer
- **Usage:** ASS subtitle document serialization (`ass_imbk`) and character encoding detection (`charset-normalizer`) used by `kbputils`.

```text
ass_imbk — The MIT License (MIT)

Copyright (c) 2014 Tony Young
Copyright (c) 2019 Myaamori
Copyright (c) 2019-2020 FichteFoll <fichtefoll2@googlemail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

```text
charset-normalizer — MIT License

Copyright (c) 2025 TAHRI Ahmed R.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## Development / Test Dependencies (Not Distributed in Builds)

- **pytest** — MIT License (https://docs.pytest.org/)

---

## LGPL Compliance Notes for Binary Distribution

When packaging Matt Joy Karaoke Builder into a standalone release (e.g., via PyInstaller or Nuitka):
1. Distribute using **dynamic linking / directory mode** (e.g., PyInstaller `--onedir`) so end users can replace the LGPL shared libraries (`Qt6*.dll`, `avcodec*.dll`, `avformat*.dll`, `avutil*.dll`, `libsndfile*.dll`) as required by LGPL-2.1 / LGPL-3.0.
2. Include both `LICENSE` and `THIRD_PARTY_LICENSES.md` in the distributed release folder/installer.
3. Do **not** bundle any GPL-compiled `ffmpeg.exe` binary; keep any external CLI tools user-supplied.
