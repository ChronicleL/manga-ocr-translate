@echo off
rem Launch Manga Studio without a console window.
setlocal
cd /d "%~dp0"
set HF_ENDPOINT=https://hf-mirror.com
start "" ".venv\Scripts\pythonw.exe" run_studio.py
endlocal
