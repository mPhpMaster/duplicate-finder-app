@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python duplicate_finder.py
if errorlevel 1 (
  echo.
  echo If python is not found, install Python 3 from https://www.python.org/
  pause
)
