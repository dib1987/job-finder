@echo off
:: Job Finder — Daily Automated Run
:: Runs non-interactive phases: parse → scrape → score → research → notify
:: Skips: approve, apply (require human input — run manually after reviewing email)
::
:: Scheduled via: python scheduler.py --install --time 08:00
:: Manual trigger: double-click this file or run from terminal

set PYTHONUTF8=1
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo [%DATE% %TIME%] Starting Job Finder daily run...

python agent.py --phase parse
if errorlevel 1 goto :error

python agent.py --phase scrape
if errorlevel 1 goto :error

python agent.py --phase score
if errorlevel 1 goto :error

python agent.py --phase research
if errorlevel 1 goto :error

python agent.py --phase notify
if errorlevel 1 goto :error

echo [%DATE% %TIME%] Daily run complete. Check your email for the report.
goto :end

:error
echo [%DATE% %TIME%] ERROR: A phase failed. Check logs/ for details.
exit /b 1

:end
exit /b 0
