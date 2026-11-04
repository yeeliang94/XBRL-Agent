@echo off
setlocal

:: Builds a source-only zip for a Veracode static scan.
:: Output lands in veracode\xbrl-agent-veracode-<date>.zip
:: Run start.bat at least once first so the venv exists.

set PYTHONUTF8=1

echo ========================================
echo   XBRL Agent — Veracode package
echo ========================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv not found. Run start.bat once first.
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: git not found on PATH. The packager uses git to list source files.
    pause
    exit /b 1
)

venv\Scripts\python.exe scripts\package_for_veracode.py %*
if errorlevel 1 (
    echo.
    echo Packaging failed.
    pause
    exit /b 1
)

echo.
echo Done. Upload the zip above in the Veracode web portal.
pause
