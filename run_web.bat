@echo off
set "PATH=%LOCALAPPDATA%\Programs\Ollama;C:\Program Files\Ollama;%PATH%"
cd /d "%~dp0"

echo ========================================================
echo   DRL Coding Agent - Portable Launcher
echo ========================================================

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

:: Check and install requirements if missing
echo Checking dependencies...
python -c "import fastapi, uvicorn, pydantic, docx, reportlab, dotenv" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing missing requirements from requirements.txt...
    python -m pip install -r requirements.txt
)

:: Start Web UI
echo Starting DRL Coding Agent Web Server...
start "" http://127.0.0.1:8000
python web_server.py

pause
