#!/usr/bin/env bash
# AI SRT Translator - launcher for macOS and Linux
# (the Windows equivalent is running_script.bat)
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.11+ is required but '$PYTHON' was not found."
    echo "macOS: brew install python   |   Ubuntu/Debian: sudo apt install python3 python3-venv"
    exit 1
fi

if [ ! -d venv ]; then
    echo "First run - creating virtual environment..."
    "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "Checking requirements..."
pip install -q --disable-pip-version-check -r requirements.txt

# Linux: pywebview needs a GUI backend. Use the system GTK bindings if present,
# otherwise install the pip-only Qt backend.
if [ "$(uname -s)" = "Linux" ] && ! python -c "import gi; gi.require_version('WebKit2', '4.1')" 2>/dev/null \
        && ! python -c "import gi; gi.require_version('WebKit2', '4.0')" 2>/dev/null; then
    if ! python -c "import qtpy, PyQt6.QtWebEngineWidgets" 2>/dev/null; then
        echo "Installing the Qt backend for pywebview (one time)..."
        pip install -q --disable-pip-version-check "pywebview[qt]"
    fi
fi

if ! python -c "import webview, google.genai, dotenv" 2>/dev/null; then
    echo
    echo "Something is missing. See the messages above."
    exit 1
fi

echo "Starting AI SRT Translator..."
exec python app.py "$@"
