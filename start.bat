@echo off
setlocal enabledelayedexpansion

:: Force Python to use UTF-8 everywhere (avoids charmap codec errors)
set PYTHONUTF8=1

echo ========================================
echo   XBRL Agent — Web UI
echo ========================================
echo.

:: ---- Check .env exists ----
if not exist ".env" (
    if exist ".env.example" (
        echo .env not found. Copying from .env.example...
        copy .env.example .env >nul
    ) else (
        echo ERROR: No .env or .env.example found.
        pause
        exit /b 1
    )
    echo.
    echo IMPORTANT: Edit .env and set your GOOGLE_API_KEY
    echo   Get it from Bruno -^> Collection -^> Auth tab -^> Vars/Secrets
    echo.
    notepad .env
    pause
)

:: ---- Find Python (may not be on PATH) ----
set "PYTHON_CMD="

:: 1. Try the Windows 'py' launcher. '-3' selects the newest registered
:: Python 3 instead of a possibly configured Python 2 or older default.
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3"
        goto :python_check_version
    )
)

:: 2. Search common supported install locations, newest first.
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python314\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python310\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%P (
        echo Found Python at %%~dpP
        set "PATH=%%~dpP;%%~dpPScripts;%PATH%"
        set "PYTHON_CMD=python"
        goto :python_check_version
    )
)

:: 3. Fall back to 'python' on PATH; the version gate below still applies.
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_check_version
)

echo ERROR: Python not found anywhere.
echo Please tell me where Python is installed on your machine
echo (e.g. C:\Users\YourName\AppData\Local\Programs\Python\Python310)
pause
exit /b 1

:python_check_version
:: Show what we found and check the version is at least 3.10. The codebase uses
:: Python 3.10+ syntax, and current PydanticAI versions do not support 3.9.
echo Found: & %PYTHON_CMD% --version
%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.10+ is required.
    pause
    exit /b 1
)

:: ---- Create venv if needed ----
:: Recreate an old or broken environment. Activation is shell-local, so later
:: commands still call the venv interpreter explicitly.
if exist "venv" if not exist "venv\Scripts\python.exe" (
    echo Existing venv is incomplete - recreating...
    rmdir /s /q "venv"
)
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if errorlevel 1 (
        echo Existing venv uses unsupported Python - recreating...
        rmdir /s /q "venv"
    )
)
if not exist "venv" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
)

:: ---- Find Node.js (installed but not on PATH) ----
where node >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\node.exe" (
        echo Found Node.js in Program Files, adding to PATH...
        set "PATH=C:\Program Files\nodejs;%PATH%"
    ) else (
        echo WARNING: Node.js not found. Frontend will not be built.
        echo If Node.js is installed elsewhere, set PATH manually.
        goto :skip_frontend
    )
)
echo Node.js: & node --version

:: Activate venv
call venv\Scripts\activate.bat

:: Install Python deps
echo Installing Python dependencies...
venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt -q

:: ---- Build frontend (if web/ exists) ----
if exist "web\package.json" (
    echo Installing frontend dependencies...
    cd web
    call npm install
    echo Building frontend...
    call npm run build
    cd ..
) else (
    echo WARNING: web/package.json not found. Skipping frontend build.
)
goto :start_server

:skip_frontend
:: Still need Python deps even if no frontend
if not exist "venv" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
)
call venv\Scripts\activate.bat
echo Installing Python dependencies...
venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt -q

:start_server
echo.
echo Starting server on http://localhost:8002
echo.
venv\Scripts\python.exe server.py

pause
