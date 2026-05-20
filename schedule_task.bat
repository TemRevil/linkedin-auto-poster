@echo off
echo ============================================================
echo   Scheduling LinkedIn Auto-Poster (Daily 5:00 PM)
echo ============================================================
echo.

REM Get the full python path
for /f "tokens=*" %%i in ('where python 2^>nul') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo ERROR: Python not found in PATH.
    pause
    exit /b 1
)

REM Get this script's directory
set SCRIPT_DIR=%~dp0

REM Create the scheduled task with full paths
schtasks /create /tn "LinkedInAutoPoster_A01" /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%scheduler.py\"" /sc daily /st 17:00 /f

if %errorlevel%==0 (
    echo.
    echo   Task created successfully!
    echo   Name: LinkedInAutoPoster_A01
    echo   Schedule: Daily at 5:00 PM
    echo   Python: %PYTHON_PATH%
    echo   Script: %SCRIPT_DIR%scheduler.py
    echo.
    echo   To test now:  python run.py test
    echo   To remove:    schtasks /delete /tn "LinkedInAutoPoster_A01" /f
    echo   To check:     schtasks /query /tn "LinkedInAutoPoster_A01"
) else (
    echo.
    echo   ERROR: Failed to create task.
    echo   Try running this script as Administrator:
    echo     Right-click schedule_task.bat -^> Run as administrator
)
echo.
pause
