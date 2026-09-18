#!/usr/bin/env bash
# Build the self-contained Linux application.
#
# Produces dist/GPD-Control -- one executable with Python, Qt and the app inside
# it. No pip, no virtualenv, nothing on PATH: the thing that runs is the file you
# double-click, which is the only way to be certain which build you are running.
#
#   ./packaging/build-linux-app.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"

rm -rf build dist/GPD-Control

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name GPD-Control \
  --windowed \
  --icon src/gpd3303s/resources/icon.png \
  --add-data "src/gpd3303s/resources/icon.png:gpd3303s/resources" \
  --add-data "src/gpd3303s/resources/icon.svg:gpd3303s/resources" \
  --paths src \
  --collect-submodules gpd3303s \
  --hidden-import serial.tools.list_ports \
  --exclude-module tkinter \
  --exclude-module PySide6.QtWebEngineCore \
  --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtPdf \
  packaging/entrypoint.py

printf '\nBuilt: %s (%s)\n' "dist/GPD-Control" \
  "$(du -h dist/GPD-Control | cut -f1)"
