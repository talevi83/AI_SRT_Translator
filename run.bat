@echo off
cd /d "%~dp0"
title AI SRT Translator

if not exist venv (
    echo First run - creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Checking requirements...
pip install -q --disable-pip-version-check -r requirements.txt

python -c "import webview, google.genai, dotenv" 2>nul
if errorlevel 1 (
    echo.
    echo Something is missing. See the messages above.
    pause
    exit /b 1
)

echo Starting AI SRT Translator...
start "" "venv\Scripts\pythonw.exe" app.py
exit /b 0
