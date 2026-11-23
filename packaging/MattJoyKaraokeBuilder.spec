# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PROJECT_ROOT = Path(SPECPATH).resolve().parent
INIT_FILE = PROJECT_ROOT / "kbp_editor" / "__init__.py"


def _read_app_version() -> str:
    text = INIT_FILE.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else "1.0.0"


def _version_tuple(ver: str) -> tuple[int, int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", ver)[:4]]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])


APP_VERSION = _read_app_version()
VER_TUPLE = _version_tuple(APP_VERSION)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=VER_TUPLE,
        prodvers=VER_TUPLE,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Matt Joy"),
                        StringStruct("FileDescription", "Matt Joy Karaoke Builder"),
                        StringStruct("FileVersion", APP_VERSION),
                        StringStruct("InternalName", "MattJoyKaraokeBuilder"),
                        StringStruct("LegalCopyright", "Copyright (c) 2026 Matt Joy. All Rights Reserved."),
                        StringStruct("OriginalFilename", "MattJoyKaraokeBuilder.exe"),
                        StringStruct("ProductName", "Matt Joy Karaoke Builder"),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

sf_datas, sf_binaries, sf_hidden = collect_all("_soundfile_data")
kbp_datas, kbp_binaries, kbp_hidden = collect_all("kbputils")

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    (str(PROJECT_ROOT / "LICENSE"), "."),
    (str(PROJECT_ROOT / "THIRD_PARTY_LICENSES.md"), "."),
] + sf_datas + kbp_datas

binaries = list(sf_binaries) + list(kbp_binaries)

hiddenimports = [
    "kbputils",
    "ass",
    "charset_normalizer",
    "soundfile",
    "_soundfile_data",
] + sf_hidden + kbp_hidden

excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtOpenGLWidgets",
    "pytest",
    "_pytest",
    "tkinter",
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MattJoyKaraokeBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "app_icon.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MattJoyKaraokeBuilder",
)
