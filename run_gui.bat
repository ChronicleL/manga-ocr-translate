@echo off
rem Launch the floating manga text extractor without a console window.
setlocal
cd /d "%~dp0"
set HF_ENDPOINT=https://hf-mirror.com
start "" ".venv\Scripts\pythonw.exe" run_gui.py
endlocal
