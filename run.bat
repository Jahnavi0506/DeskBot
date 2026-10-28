@echo off
set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
cd /d "%~dp0"
python agent.py %*
