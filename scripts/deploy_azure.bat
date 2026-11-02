@echo off
setlocal enabledelayedexpansion

:: ===========================================================================
::  deploy_azure.bat — guarded manual deploy to Azure App Service
::  (PLAN-azure-auth-deployment Phase 3 step 5). Run from the enterprise
::  Windows laptop — the only machine with both the code and Azure access.
::
::  GitHub Actions CANNOT deploy here: that would require enterprise Azure
::  credentials inside a personal GitHub repo (policy violation). This script
::  is the primary path; raw `az webapp deploy` is unguarded (no tests, stale
::  dist) — always use this.
::
::  Steps (aborts on ANY failure):
::    1. warn if the working tree has uncommitted changes
::    2. npm ci + npm run build   (fresh web/dist -> repo-root/dist)
::    3. backend pytest + frontend vitest
::    4. stamp the commit hash into version.txt
::    5. stage + zip (exclude output/, .env, node_modules, backup-originals/;
::       include the XBRL-template-* and SSMxT_2022v1.0 runtime data)
::    6. az webapp deploy --type zip
:: ===========================================================================

:: Force UTF-8 everywhere (gotcha #1) — PDFs carry Unicode that crashes the
:: default charmap codec.
set PYTHONUTF8=1

:: ---- Azure target (edit these once Phase 3 provisioning is done) ----
:: Overridable from the environment so secrets/names aren't hard-baked.
if "%AZ_RESOURCE_GROUP%"=="" set "AZ_RESOURCE_GROUP=rg-xbrl-agent"
if "%AZ_APP_NAME%"=="" set "AZ_APP_NAME=CHANGE-ME-app-name"

:: Corporate proxy MITM (gotcha #5): the az CLI may need these. Uncomment and
:: point at the corporate root CA once the working values are known.
:: set "HTTPS_PROXY=http://proxy.corp:8080"
:: set "REQUESTS_CA_BUNDLE=C:\path\to\corporate-root-ca.pem"

cd /d "%~dp0\.."
echo ========================================
echo   Deploy XBRL Agent to Azure App Service
echo   Resource group: %AZ_RESOURCE_GROUP%
echo   App name:       %AZ_APP_NAME%
echo ========================================
echo.

if "%AZ_APP_NAME%"=="CHANGE-ME-app-name" (
    echo ERROR: Choose the target app first. In this window, run for example:
    echo          set AZ_RESOURCE_GROUP=rg-xbrl-agent-test
    echo          set AZ_APP_NAME=xbrl-agent-test
    echo        then run this script again.
    exit /b 1
)

:: ---- Locate Node.js (gotcha #8 — may not be on PATH) ----
where node >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\node.exe" (
        set "PATH=C:\Program Files\nodejs;%PATH%"
    ) else (
        echo ERROR: Node.js not found. Install it or add it to PATH.
        exit /b 1
    )
)

:: ---- 1. Warn on a dirty working tree ----
git diff --quiet && git diff --cached --quiet
if errorlevel 1 (
    echo WARNING: You have uncommitted changes. Deploying them means "what is
    echo          live" won't match any commit.
    set /p CONFIRM="Continue anyway? [y/N] "
    if /i not "!CONFIRM!"=="y" (
        echo Aborted.
        exit /b 1
    )
)

:: ---- 2. Build the frontend (fresh dist) ----
echo.
echo [2/6] Building frontend...
pushd web
call npm ci
if errorlevel 1 ( echo ERROR: npm ci failed. & popd & exit /b 1 )
call npm run build
if errorlevel 1 ( echo ERROR: frontend build failed. & popd & exit /b 1 )
popd

:: ---- 3. Run the test suites ----
echo.
echo [3/6] Running tests...
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv || ( echo ERROR: venv creation failed. & exit /b 1 )
)
call venv\Scripts\activate.bat
pip install -r requirements.txt -q
python -m pytest tests\ -q
if errorlevel 1 ( echo ERROR: backend tests failed. & exit /b 1 )
pushd web
call npx vitest run
if errorlevel 1 ( echo ERROR: frontend tests failed. & popd & exit /b 1 )
popd

:: ---- 4. Stamp the commit hash so "what's live?" stays answerable ----
echo.
echo [4/6] Stamping version...
for /f %%H in ('git rev-parse --short HEAD') do set "GIT_HASH=%%H"
echo %GIT_HASH%> version.txt
echo Deploying commit %GIT_HASH%.

:: ---- 5. Stage + zip ----
echo.
echo [5/6] Packaging...
set "STAGE=%TEMP%\xbrl-deploy-stage"
set "PKG=%TEMP%\xbrl-deploy.zip"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%PKG%" del /q "%PKG%"
:: robocopy mirrors the tree minus the excluded dirs/files. /XD excludes a dir
:: by name anywhere in the tree (so backup-originals under XBRL-template-* go);
:: the XBRL-template-* + SSMxT_2022v1.0 runtime data is copied by default.
:: Ignored private material (.env files, data, logs, databases, working
:: documents) must never enter the package; the app reads nothing from docs.
:: robocopy exit codes 0-7 are success; >=8 is a real error.
robocopy . "%STAGE%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD output node_modules .git venv .pytest_cache web\node_modules backup-originals __pycache__ .vscode ^
        .claude .idea XBRL-Agent data logs ab_work .codex-remote-attachments "%CD%\docs" ^
    /XF .env .env.* *.pyc *.log *.db *.db-wal *.db-shm
if %ERRORLEVEL% GEQ 8 ( echo ERROR: staging copy failed. & exit /b 1 )
:: Python's zipfile writes forward-slash entry names. Windows PowerShell 5.1
:: Compress-Archive writes backslashes, which Linux App Service unpacks as
:: flat files named "web\dist\index.html" instead of folders.
python -c "import shutil, sys; shutil.make_archive(sys.argv[1][:-4], 'zip', sys.argv[2])" "%PKG%" "%STAGE%"
if errorlevel 1 ( echo ERROR: zip failed. & exit /b 1 )
echo Package: %PKG%

:: ---- 6. Deploy ----
echo.
echo [6/6] Deploying to Azure...
where az >nul 2>&1
if errorlevel 1 (
    echo The Azure CLI ^(az^) is not installed on this laptop. The package is
    echo built; deploy it from Azure Cloud Shell instead:
    echo   1. Open https://portal.azure.com, click the Cloud Shell icon, choose Bash.
    echo   2. Manage files ^> Upload, and pick %PKG%
    echo   3. Run:
    echo      az webapp deploy --resource-group %AZ_RESOURCE_GROUP% --name %AZ_APP_NAME% --src-path xbrl-deploy.zip --type zip
    echo Kudu's drag-and-drop zip upload does not work for Linux apps.
    exit /b 1
)
az webapp deploy --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_APP_NAME%" --type zip --src-path "%PKG%"
if errorlevel 1 ( echo ERROR: az webapp deploy failed. & exit /b 1 )

echo.
echo ========================================
echo   Deployed commit %GIT_HASH% to %AZ_APP_NAME%.
echo   Reminder: seed accounts on first deploy with
echo     python -m auth.manage add-user EMAIL --name "..."
echo ========================================
endlocal
