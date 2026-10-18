@echo off
REM edict-sim launcher.
REM Python: %EDICT_PYTHON% -> ..\.venv -> python on PATH
REM Usage:  start.bat            start server and open browser
REM         start.bat test       run offline self-check

setlocal
cd /d "%~dp0"
set "PYTHONPATH="

set "PY=%EDICT_PYTHON%"
if not defined PY if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

if /i "%~1"=="test" (
  "%PY%" main.py test
  exit /b %ERRORLEVEL%
)

start "" http://127.0.0.1:9031
"%PY%" main.py %*
