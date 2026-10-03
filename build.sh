#!/bin/bash
# ============================================================
# DicomBridge - Linux build: venv + deps + tests + PyInstaller
# (ASCII-only on purpose)
# Usage: ./build.sh
# Result: dist/DicomBridge
# Then: ./install.sh  (user-local install, no sudo)
# ============================================================
set -e
cd "$(dirname "$0")"

echo "[1/4] Checking Python..."
python3 --version

echo "[2/4] Virtualenv and dependencies..."
if [ ! -f ".venv/bin/activate" ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[3/4] Running tests..."
python -m pytest tests -q

echo "[4/4] Building binary (takes several minutes)..."
python -m PyInstaller --onefile --noconsole --name "DicomBridge" \
  --icon=assets/app.ico \
  --add-data="assets:assets" \
  --hidden-import=pynetdicom \
  --hidden-import=pynetdicom.sop_class \
  --hidden-import=pynetdicom.presentation \
  --hidden-import=pydicom \
  --hidden-import=pydicom.datadict \
  --hidden-import=pydicom.uid \
  --hidden-import=watchdog.observers \
  --collect-all=pynetdicom \
  --collect-all=pydicom \
  --collect-all=PySide6 \
  main.py

echo "DONE: dist/DicomBridge"
