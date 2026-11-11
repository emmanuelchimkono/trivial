@echo off
rem Double-click this file to start Trivial on Windows.
cd /d "%~dp0"
title Trivial

where py >nul 2>nul
if errorlevel 1 (
    echo Python isn't installed. Install it from python.org ^(tick "Add python.exe to PATH"^), then try again.
    pause
    exit /b 1
)

if not exist ".streamlit\secrets.toml" (
    copy ".streamlit\secrets.toml.example" ".streamlit\secrets.toml" >nul
    echo.
    echo FIRST TIME SETUP
    echo Notepad will open. Fill in APP_PASSWORD, ADMIN_PASSWORD and ANTHROPIC_API_KEY,
    echo keep the quote marks, then press Ctrl+S to save and close Notepad.
    echo.
    start /wait notepad ".streamlit\secrets.toml"
)

echo Checking packages (the first time this can take a few minutes)...
py -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installing packages failed. Send the red text above to Claude for help.
    pause
    exit /b 1
)

echo.
echo Starting Trivial. Your browser will open in a moment.
echo Keep this window open while you use Trivial. Close it to stop Trivial.
echo.
py -m streamlit run app.py
pause
