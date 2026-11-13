@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   ScoreMe Configurable Workflow Decision Platform (Windows)
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    python -m venv .venv
)

echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

echo [3/3] Starting FastAPI server + Web UI at http://127.0.0.1:8000
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
