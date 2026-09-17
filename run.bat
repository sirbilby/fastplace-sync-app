@echo off
setlocal
cd /d "%~dp0"

echo Checking for Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3 from https://www.python.org/
    pause
    exit /b 1
)

echo Checking and installing required dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install opencv-python numpy Pillow av

echo Starting Fast-Place Sync...
python app.py
