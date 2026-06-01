@echo off
REM ============================================================
REM  ViniCut-AI  -  FULL SYSTEM launcher
REM  Starts Ollama (if needed) + the FastAPI backend (web UI at
REM  http://127.0.0.1:8000 + queue + watch folder) + the browser.
REM ============================================================
title ViniCut-AI Studio
cd /d "%~dp0"

REM --- Optional: activate a local virtualenv if you use one ---
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
if exist "env\Scripts\activate.bat" call "env\Scripts\activate.bat"

REM --- Find a Python that actually has FastAPI installed ---
set "PY="
for %%P in (python py python3) do (
  if not defined PY (
    %%P -c "import fastapi" >nul 2>nul && set "PY=%%P"
  )
)
if not defined PY (
  echo.
  echo  ERROR: No Python with FastAPI was found.
  echo  Fix one of these, then re-run:
  echo    * If you use a virtualenv/conda env, activate it first, OR
  echo    * Edit this file and set PY to your python.exe full path, OR
  echo    * Install deps:
  echo        python -m pip install fastapi uvicorn python-multipart pydantic ollama faster-whisper requests
  echo.
  pause
  exit /b 1
)
echo Using Python:
%PY% -c "import sys;print('   '+sys.executable)"

REM --- Free port 8000 if a previous backend is still running ---
for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do (
  echo Stopping stale server on port 8000 (PID %%A)...
  taskkill /PID %%A /F >nul 2>nul
)

REM --- Start Ollama only if nothing is listening on 11434 ---
netstat -ano | findstr ":11434" >nul 2>nul
if errorlevel 1 (
  echo [1/2] Starting Ollama service...
  start "Ollama" /min ollama serve
  ping -n 6 127.0.0.1 >nul
) else (
  echo [1/2] Ollama already running.
)

echo [2/2] Starting ViniCut-AI backend...
echo.
echo   ViniCut-AI Studio   ->   http://127.0.0.1:8000
echo   (close this window or press Ctrl+C to stop)
echo.

REM --- Open the browser ~4s after the backend starts ---
start "" /min cmd /c "ping -n 5 127.0.0.1 >nul & start http://127.0.0.1:8000"

REM --- Start the backend (serves UI + queue + watch folder) ---
%PY% main.py

echo.
echo Server stopped.
pause >nul
