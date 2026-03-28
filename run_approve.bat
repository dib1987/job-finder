@echo off
:: Job Finder — Approval Gate
:: Run this after receiving the daily email report.
:: Review each matched job and press:
::   A = Approve (add to apply list)
::   S = Skip
::   Q = Quit (saves progress)

set PYTHONUTF8=1
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo.
echo =============================================
echo  Job Finder - Approval Gate
echo =============================================
echo  Review jobs from today's report.
echo  A = Approve   S = Skip   Q = Quit
echo =============================================
echo.

python agent.py --phase approve
if errorlevel 1 (
    echo.
    echo ERROR: Approval phase failed. Check logs/ for details.
    pause
    exit /b 1
)

echo.
echo Approval complete. Run apply when ready:
echo   python agent.py --phase apply
echo.
pause
