@echo off
:: Job Finder — Daily Automated Run
:: Runs non-interactive phases only: parse → scrape → score → research → notify
:: Skips: approve, apply (require human input — run manually after reviewing email)
::
:: Scheduled via: python scheduler.py --install --time 08:00
:: Manual trigger: double-click this file or run from terminal

set PYTHONUTF8=1
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo [%DATE% %TIME%] Starting Job Finder daily run...

python agent.py --daily
if errorlevel 1 (
    echo [%DATE% %TIME%] ERROR: Daily run failed. Check logs/ for details.
    python -c "
from dotenv import load_dotenv; load_dotenv()
from tools.email_tool import send_email
send_email(
    subject='Job Finder ERROR — Daily run failed',
    html='<p>The Job Finder daily run failed today. Check <code>logs/</code> on your laptop for details.</p>',
    plain='Job Finder daily run failed. Check logs/ on your laptop.'
)
"
    exit /b 1
)

echo [%DATE% %TIME%] Daily run complete. Check your email for the report.
exit /b 0
