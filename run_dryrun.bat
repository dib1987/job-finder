@echo off
cd /d "c:\Agentic Workflow\Job Finder"
echo.
echo =============================================
echo  Job Agent - Dry Run (no applications sent)
echo =============================================
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --dry-run
echo.
pause
