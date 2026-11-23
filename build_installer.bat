@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ========================================================================
echo   Matt Joy Karaoke Builder - One-Click Installer Build
echo ========================================================================
echo.

REM --- 1. Locate Python in venv ---
set "PYTHON=venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Virtual environment not found at "%~dp0%PYTHON%".
    echo         Create it first with: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    goto :fail
)

REM --- 2. Read version automatically from kbp_editor/__init__.py ---
for /f "usebackq delims=" %%V in (`venv\Scripts\python.exe -c "from kbp_editor import __version__; print(__version__)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
    echo [ERROR] Could not read __version__ from kbp_editor\__init__.py.
    goto :fail
)
echo [1/5] Detected app version: v%APP_VERSION%

REM --- 3. Ensure PyInstaller is installed in venv ---
"%PYTHON%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller not found in venv - installing...
    "%PYTHON%" -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller into venv.
        goto :fail
    )
)

REM --- 4. Locate Inno Setup Compiler (ISCC.exe) ---
set "ISCC="
for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
    "C:\Program Files\Inno Setup 5\ISCC.exe"
) do (
    if not defined ISCC if exist %%~P set "ISCC=%%~P"
)
if not defined ISCC (
    for /f "delims=" %%I in ('where iscc.exe 2^>nul') do if not defined ISCC set "ISCC=%%I"
)
if not defined ISCC (
    echo [ERROR] Inno Setup Compiler ^(ISCC.exe^) was not found.
    echo         Please install Inno Setup 6 from https://jrsoftware.org/isinfo.php
    goto :fail
)
echo [2/5] Found Inno Setup: "%ISCC%"

REM --- 5. Clean previous temporary build directories ---
echo [3/5] Running PyInstaller...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

"%PYTHON%" -m PyInstaller --noconfirm --clean "packaging\MattJoyKaraokeBuilder.spec"
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    goto :fail
)

REM --- 6. Compile Inno Setup Installer with auto-injected version ---
echo.
echo [4/5] Building Inno Setup installer for v%APP_VERSION%...
"%ISCC%" "/DMyAppVersion=%APP_VERSION%" "packaging\installer.iss"
if errorlevel 1 (
    echo [ERROR] Inno Setup compilation failed.
    goto :fail
)

REM --- 7. Clean up residual PyInstaller files ---
echo.
echo [5/5] Cleaning up residual build files...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo.
echo ========================================================================
echo   SUCCESS - Installer ready:
echo   %~dp0release\MattJoyKaraokeBuilder_Setup_v%APP_VERSION%.exe
echo ========================================================================
echo.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail
echo.
echo ========================================================================
echo   BUILD FAILED. See error message above.
echo ========================================================================
echo.
if "%NO_PAUSE%"=="0" pause
exit /b 1
