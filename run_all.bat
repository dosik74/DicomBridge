@echo off
REM ============================================================
REM  DicomBridge - run_all.bat: runs ALL steps in sequence
REM  (ASCII-only: cmd.exe parses .bat in OEM codepage, so no
REM   Cyrillic here - otherwise lines get garbled and fail)
REM   1. Check Python
REM   2. Create .venv (if missing) + install requirements
REM   3. Run tests (pytest)
REM   4. Generate test DICOM files
REM   5. Build .exe via build.bat
REM
REM  Skip steps via environment variables:
REM    set SKIP_TESTS=1   - skip tests
REM    set SKIP_DICOMS=1  - skip test DICOM generation
REM    set SKIP_BUILD=1   - skip .exe build
REM    set RUN_APP=1      - launch the app at the end
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/5] Checking Python...
python --version
if errorlevel 1 (
  echo ERROR: Python not found in PATH. Install Python 3.11+ from python.org
  echo and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

echo.
echo [2/5] Virtualenv and dependencies...
if not exist ".venv\Scripts\activate.bat" (
  echo Creating .venv...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: could not create .venv
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: dependency installation failed
  pause
  exit /b 1
)

if defined SKIP_TESTS (
  echo.
  echo [3/5] Tests SKIPPED ^(SKIP_TESTS=1^)
) else (
  echo.
  echo [3/5] Running tests...
  python -m pytest tests -q
  if errorlevel 1 (
    echo ERROR: tests failed. See output above.
    pause
    exit /b 1
  )
  echo Tests passed.
)

if defined SKIP_DICOMS (
  echo.
  echo [4/5] Test DICOM generation SKIPPED ^(SKIP_DICOMS=1^)
) else (
  echo.
  echo [4/5] Generating test DICOM files...
  python tools\make_test_dicoms.py --out test_dicoms --count 3
  if errorlevel 1 (
    echo WARNING: test file generator returned an error
  )
)

if defined SKIP_BUILD (
  echo.
  echo [5/5] .exe build SKIPPED ^(SKIP_BUILD=1^)
) else (
  echo.
  echo [5/5] Building .exe ^(takes several minutes^)...
  call build.bat
  if errorlevel 1 (
    echo ERROR: build failed. See output above.
    pause
    exit /b 1
  )
)

echo.
echo ============================================================
echo ALL DONE.
if not defined SKIP_BUILD (
  echo exe: dist\DicomBridge.exe
)
echo Manual start: python main.py
echo ============================================================

if defined RUN_APP (
  echo Starting the app...
  python main.py
)

pause
