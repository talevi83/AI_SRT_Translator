#!/usr/bin/env bash
# Build a standalone app for macOS (.app) or Linux (single binary)
# (the Windows equivalent is build.bat)
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing requirements and PyInstaller..."
# Pillow lets PyInstaller convert icon.ico to the .icns format macOS needs
pip install -q --disable-pip-version-check -r requirements.txt pyinstaller pillow

NAME="AI SRT Translator"
COMMON=(--noconfirm --clean --windowed --name "$NAME" --icon icon.ico
        --add-data "web:web" --add-data "icon.ico:." --collect-data webview)

echo
echo "Building - this takes a minute or two..."
if [ "$(uname -s)" = "Darwin" ]; then
    # macOS: an .app bundle (onedir - onefile .app bundles are deprecated)
    pyinstaller "${COMMON[@]}" --osx-bundle-identifier com.aisrt.translator app.py
    OUT="dist/$NAME.app"
else
    pyinstaller "${COMMON[@]}" --onefile app.py
    OUT="dist/$NAME"
fi

rm -rf build "$NAME.spec"

echo
echo "=================================================="
echo " Done!  $OUT"
if [ "$(uname -s)" = "Darwin" ]; then
    # The .app keeps its settings outside the bundle
    SUPPORT="$HOME/Library/Application Support/$NAME"
    mkdir -p "$SUPPORT"
    [ -f .env ] && [ ! -f "$SUPPORT/.env" ] && cp .env "$SUPPORT/.env"
    echo " Settings (.env) and logs are kept in:"
    echo "   $SUPPORT"
else
    # Keep the API key and settings next to the binary
    [ -f .env ] && [ ! -f "dist/.env" ] && cp .env "dist/.env"
fi
echo "=================================================="
