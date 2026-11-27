@echo off
REM AI Learning Engine - Windows launcher. First run creates .venv and installs dependencies.
REM Put your Claude API key in a file named .env next to this script:  CLAUDE_API_KEY=sk-ant-...
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
if exist .env for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
start "" http://localhost:8000
uvicorn lengine.api:app --port 8000
