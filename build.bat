@echo off
REM === DicomBridge: build single-file .exe with PyInstaller (ASCII-only) ===
setlocal
cd /d "%~dp0"

python -m pip install --upgrade pip
pip install -r requirements.txt

REM Optional: run tests before build
REM python -m pytest tests -q

set APPNAME=DicomBridge

pyinstaller --onefile --noconsole --name "%APPNAME%" ^
  --hidden-import=pynetdicom ^
  --hidden-import=pynetdicom.sop_class ^
  --hidden-import=pynetdicom.presentation ^
  --hidden-import=pydicom ^
  --hidden-import=pydicom.datadict ^
  --hidden-import=pydicom.uid ^
  --hidden-import=watchdog.observers ^
  --hidden-import=watchdog.observers.read_directory_changes ^
  --collect-all=pynetdicom ^
  --collect-all=pydicom ^
  --collect-all=PySide6 ^
  main.py

echo.
echo DONE: dist\%APPNAME%.exe
pause
