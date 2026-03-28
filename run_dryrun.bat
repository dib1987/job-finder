@echo off
:: Job Finder — Dry Run
:: Runs the full pipeline (parse → scrape → score → research → notify)
:: without submitting any applications. Use this to test the system.

set PYTHONUTF8=1
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo.
echo =============================================
echo  Job Finder - Dry Run (no applications sent)
echo =============================================
echo.

python agent.py --dry-run
echo.
pause
