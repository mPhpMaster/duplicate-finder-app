@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo Installing PyInstaller if needed...
python -m pip install --upgrade pip pyinstaller -q
if errorlevel 1 (
  echo Failed to install PyInstaller. Is Python on PATH?
  pause
  exit /b 1
)

echo.
echo Building Duplicate File Finder.exe ...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "Duplicate File Finder" ^
  duplicate_finder.py

if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)

echo.
choice /C YN /T 5 /D N /M "Update Preview.png (runs scripts\capture_preview.py)"
if not errorlevel 2 (
  echo.
  echo Capturing Preview.png ...
  python -m pip install pillow -q
  python scripts/capture_preview.py
  if errorlevel 1 (
    echo Preview capture failed.
  ) else (
    echo Preview.png updated.
  )
)

echo.
choice /C YN /T 5 /D N /M "Update dist\DuplicateFileFinder-Setup.exe (Inno Setup)"
if not errorlevel 2 (
  set "ISCC="
  if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  ) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
  )
  if "!ISCC!"=="" (
    echo Inno Setup 6 not found. Install from https://jrsoftware.org/isdl.php
  ) else (
    echo.
    echo Building DuplicateFileFinder-Setup.exe ...
    "!ISCC!" dup-installer.iss
    if errorlevel 1 (
      echo Installer build failed.
    ) else (
      echo Installer updated: %~dp0dist\DuplicateFileFinder-Setup.exe
    )
  )
)

echo.
echo Done. Executable:
echo   %~dp0dist\Duplicate File Finder.exe
if exist "%~dp0dist\DuplicateFileFinder-Setup.exe" (
  echo Installer:
  echo   %~dp0dist\DuplicateFileFinder-Setup.exe
)
echo.
echo Copy the .exe anywhere; settings are saved beside the .exe.
pause
