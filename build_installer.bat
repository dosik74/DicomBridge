@echo off
REM ============================================================
REM DicomBridge - build_installer.bat: builds setup with version
REM taken from config.py (single source of truth), so installer
REM version always matches the GitHub release. ASCII-only.
REM Usage: build_installer.bat   (needs dist\DicomBridge.exe)
REM Result: setup\DicomBridge-Setup.exe
REM ============================================================
setlocal
cd /d "%~dp0"

for /f "tokens=2 delims==" %%a in ('findstr "^APP_VERSION" config.py') do set RAW=%%a
set VER=%RAW:"=%
set VER=%VER: =%
if "%VER%"=="" (
  echo ERROR: could not read APP_VERSION from config.py
  pause
  exit /b 1
)
echo Version: %VER%

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" (
  echo ERROR: Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)
if not exist "dist\DicomBridge.exe" (
  echo ERROR: dist\DicomBridge.exe missing. Run build.bat first.
  pause
  exit /b 1
)

"%ISCC%" /DMyAppVersion=%VER% installer\installer.iss
if errorlevel 1 (
  echo ERROR: installer build failed
  pause
  exit /b 1
)
echo DONE: setup\DicomBridge-Setup.exe (version %VER%)
pause
