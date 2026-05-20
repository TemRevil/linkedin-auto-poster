@echo off
REM ============================================================
REM   LinkedIn AutoPoster - Windows Task Scheduler Setup
REM ============================================================
REM   Registers two daily scheduled tasks:
REM     1. LinkedinA01_Discovery - scrapes news at discovery_time
REM     2. LinkedinA01_Generate  - generates post via Claude Code CLI at generation_time
REM
REM   Times are read from settings.json (defaults: 15:00 and 17:00).
REM   Re-run this script after changing times in the Settings UI.
REM
REM   Tasks run as the current user, so Claude Code authentication
REM   (stored in your home dir) is available automatically.
REM ============================================================

setlocal enabledelayedexpansion

REM --- Resolve script directory (project root) ---
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

REM --- Find Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found on PATH.
    echo Install Python or fix your PATH, then re-run.
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do (
    set "PYTHON_EXE=%%i"
    goto :python_found
)
:python_found

REM --- Find Claude Code CLI (for the 5 PM generation step) ---
where claude >nul 2>&1
if errorlevel 1 (
    echo [WARNING] 'claude' CLI not found on PATH.
    echo Generation step at 5 PM will fall back to template mode.
) else (
    echo [OK] Claude Code CLI detected.
)

REM --- Read times from settings.json (fallback to defaults) ---
set "DISCOVERY_TIME=15:00"
set "GENERATE_TIME=17:00"

if exist "%PROJECT_DIR%\settings.json" (
    for /f "tokens=*" %%a in ('%PYTHON_EXE% -c "import json; s=json.load(open(r'%PROJECT_DIR%\settings.json', encoding='utf-8')); print(s.get('discovery_time', '15:00'))" 2^>nul') do (
        set "DISCOVERY_TIME=%%a"
    )
    for /f "tokens=*" %%a in ('%PYTHON_EXE% -c "import json; s=json.load(open(r'%PROJECT_DIR%\settings.json', encoding='utf-8')); print(s.get('generation_time', '17:00'))" 2^>nul') do (
        set "GENERATE_TIME=%%a"
    )
)

echo.
echo ============================================================
echo   Setting up LinkedIn AutoPoster scheduled tasks
echo ============================================================
echo   Project: %PROJECT_DIR%
echo   Python:  %PYTHON_EXE%
echo   Discovery time: %DISCOVERY_TIME%   (daily news scrape)
echo   Generate time:  %GENERATE_TIME%   (daily Claude post)
echo ============================================================
echo.

REM --- Delete any existing tasks (clean slate) ---
schtasks /Delete /TN "LinkedinA01_Discovery" /F >nul 2>&1
schtasks /Delete /TN "LinkedinA01_Generate" /F >nul 2>&1
schtasks /Delete /TN "LinkedinA01_Startup" /F >nul 2>&1

REM --- Create Discovery task (daily news scrape) ---
schtasks /Create /TN "LinkedinA01_Discovery" /TR "\"%PYTHON_EXE%\" \"%PROJECT_DIR%\scheduler.py\" --discover" /SC DAILY /ST %DISCOVERY_TIME% /RL LIMITED /F

if errorlevel 1 (
    echo [ERROR] Failed to create discovery task.
    pause
    exit /b 1
) else (
    echo [OK] Discovery task created - runs daily at %DISCOVERY_TIME%
)

REM --- Create Generation task (daily Claude post) ---
schtasks /Create /TN "LinkedinA01_Generate" /TR "\"%PYTHON_EXE%\" \"%PROJECT_DIR%\scheduler.py\" --generate-via-claude" /SC DAILY /ST %GENERATE_TIME% /RL LIMITED /F

if errorlevel 1 (
    echo [ERROR] Failed to create generation task.
    pause
    exit /b 1
) else (
    echo [OK] Generation task created - runs daily at %GENERATE_TIME%
)

REM --- Add launcher to Windows Startup folder (auto-launch on logon) ---
REM Using the Startup folder is simpler than ONLOGON scheduled task (no admin needed)
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_VBS=%STARTUP_DIR%\LinkedinA01_Launcher.vbs"

REM Use a VBS wrapper to launch python silently (no console window)
> "%STARTUP_VBS%" echo Set WshShell = CreateObject("WScript.Shell")
>> "%STARTUP_VBS%" echo WshShell.Run """%PYTHON_EXE%"" ""%PROJECT_DIR%\_start.py""", 0, False

if exist "%STARTUP_VBS%" (
    echo [OK] Startup launcher installed - dashboard auto-launches on Windows logon
    echo      Location: %STARTUP_VBS%
) else (
    echo [WARN] Could not install startup launcher
)

echo.
echo ============================================================
echo   DONE. Daily tasks + startup launcher installed.
echo ============================================================
echo.
echo   View tasks:    schtasks /Query /TN "LinkedinA01_Discovery"
echo                  schtasks /Query /TN "LinkedinA01_Generate"
echo.
echo   Run manually:  schtasks /Run /TN "LinkedinA01_Discovery"
echo                  schtasks /Run /TN "LinkedinA01_Generate"
echo.
echo   Delete:        schtasks /Delete /TN "LinkedinA01_Discovery" /F
echo                  schtasks /Delete /TN "LinkedinA01_Generate" /F
echo                  del "%STARTUP_VBS%"
echo.
echo   Or open Task Scheduler GUI (taskschd.msc) to inspect / edit.
echo.
endlocal
