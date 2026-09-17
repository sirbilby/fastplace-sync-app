#!/usr/bin/env bash
cd "$(dirname "$0")"

echo "Checking Python 3 availability..."
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 could not be found."
    echo "Please install Python 3 via https://www.python.org/ or 
Homebrew."
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Checking and installing required dependencies..."
python3 -m pip install --upgrade pip > /dev/null 2>&1
python3 -m pip install opencv-python numpy Pillow av

# macOS Tkinter check
python3 -c "import tkinter" &> /dev/null
if [ $? -ne 0 ]; then
    echo "[ERROR] Tkinter is not installed for python3."
    echo "Install it via brew: brew install python-tk"
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Starting Fast-Place Sync..."
python3 app.py
