@echo off
echo ============================================================
echo   LinkedIn Auto-Poster - Setup
echo ============================================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
echo.

echo [2/3] Installing Playwright browser...
playwright install chromium
echo.

echo [3/3] Setup complete!
echo.
echo ============================================================
echo   NEXT STEPS:
echo ============================================================
echo.
echo   1. Start the dashboard and run the first-run setup wizard:
echo      python src\run.py approve
echo.
echo   2. Login to LinkedIn (one-time):
echo      python src\linkedin_poster.py --login
echo.
echo   3. (Optional) Setup Gmail for newsletter scraping:
echo      python src\gmail_setup.py
echo.
echo   4. Schedule daily runs:
echo      setup_scheduled_tasks.bat
echo.
echo   5. Or run manually:
echo      python src\scheduler.py
echo.
echo ============================================================
pause
