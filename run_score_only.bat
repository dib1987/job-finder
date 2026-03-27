@echo off
cd /d "c:\Agentic Workflow\Job Finder"
echo.
echo =============================================
echo  Job Agent - Re-parse + Re-score (no scrape)
echo =============================================
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase parse
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase score
echo.
pause
