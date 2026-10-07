@echo off
cd /d "%~dp0"
title Building AI SRT Translator

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo Installing requirements and PyInstaller...
pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo Building EXE - this takes a minute or two...
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name "AI SRT Translator" ^
    --icon icon.ico ^
    --add-data "web;web" ^
    --add-data "icon.ico;." ^
    --collect-data webview ^
    app.py
if errorlevel 1 goto :error

rem Keep the API key and settings next to the EXE
if exist .env if not exist "dist\.env" copy /y .env "dist\.env" >nul
if exist "AI SRT Translator.spec" del "AI SRT Translator.spec"
if exist build rmdir /s /q build

echo.
echo ==================================================
echo  Done!  dist\AI SRT Translator.exe
echo ==================================================
explorer dist
pause
exit /b 0

:error
echo.
echo Build failed - see the messages above.
pause
exit /b 1
