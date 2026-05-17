@echo off
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
echo Done. Executable:
echo   %~dp0dist\Duplicate File Finder.exe
echo.
echo Copy the .exe anywhere; settings are saved beside the .exe.
pause
