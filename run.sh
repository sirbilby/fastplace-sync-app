#!/usr/bin/env bash
cd "$(dirname "$0")"

echo "Checking Python 3 availability..."
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed."
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Ensuring pip is available..."
python3 -m pip --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[ERROR] pip is missing. Please install python3-pip using your 
system package manager."
    read -p "Press Enter to exit..."
    exit 1
fi

# Linux distros usually break Tkinter out into a separate system package 
(python3-tk)
python3 -c "import tkinter" &> /dev/null
if [ $? -ne 0 ]; then
    echo "[WARNING] Tkinter not detected. Attempting to run, but if it 
fails, install python3-tk via apt/dnf."
fi

echo "Installing required Python packages..."
python3 -m pip install opencv-python numpy Pillow av

echo "Starting Fast-Place Sync..."
python3 app.py
