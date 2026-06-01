@echo off
REM ============================================================
REM  ViniCut-AI  -  FULL SYSTEM launcher
REM  1) Starts Ollama (only if it isn't already running)
REM  2) Starts the FastAPI backend  ->  web UI at :8000
REM     (also runs the processing queue + watch folder)
REM  3) Opens the browser to the UI
REM ============================================================
title ViniCut-AI Studio
cd /d "%~dp0"

REM --- Optional: activate a local virtualenv if you use one ---
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

REM --- Choose a Python launcher (prefer the 'py' launcher) ---
set "PY=python"
where py >nul 2>nul && set "PY=py"

REM --- Start Ollama only if nothing is listening on port 11434 ---
netstat -ano | findstr ":11434" >nul 2>nul
if errorlevel 1 (
  echo [1/2] Starting Ollama service...
  start "Ollama" /min ollama serve
  REM give the daemon a few seconds to come up
  ping -n 6 127.0.0.1 >nul
) else (
  echo [1/2] Ollama already running.
)

echo [2/2] Starting ViniCut-AI backend...
echo.
echo   ViniCut-AI Studio
echo   ------------------------------------------
echo   UI / API : http://127.0.0.1:8000
echo   Stop     : close this window or press Ctrl+C
echo.

REM --- Open the browser ~4s after the backend starts (detached) ---
start "" /min cmd /c "ping -n 5 127.0.0.1 >nul & start http://127.0.0.1:8000"

REM --- Start the backend (serves UI + queue + watch folder) ---
REM     This window stays open while it runs; logs appear here.
%PY% main.py

echo.
echo Server stopped.
pause >nul
