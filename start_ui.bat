@echo off
REM ============================================================
REM  ViniCut-AI Studio  -  launches the backend + new web UI
REM  The FastAPI backend serves the UI at http://127.0.0.1:8000
REM  (from webui\index.html), plus the queue + watch-folder.
REM ============================================================
title ViniCut-AI Studio
cd /d "%~dp0"

REM --- Optional: activate a local virtualenv if you use one ---
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

REM --- Choose a Python launcher (prefer the 'py' launcher) ---
set "PY=python"
where py >nul 2>nul && set "PY=py"

echo.
echo   ViniCut-AI Studio
echo   ------------------------------------------
echo   UI / API : http://127.0.0.1:8000
echo   Stop     : close this window or press Ctrl+C
echo   (Make sure Ollama is running for AI features.)
echo.

REM --- Open the browser ~4s after the server starts (detached) ---
start "" /min cmd /c "ping -n 5 127.0.0.1 >nul & start http://127.0.0.1:8000"

REM --- Start the server (this window stays open while it runs) ---
%PY% main.py

echo.
echo Server stopped.
pause >nul
