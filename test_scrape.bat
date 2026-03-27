@echo off
cd /d "c:\Agentic Workflow\Job Finder"
echo.
echo =============================================
echo  Job Agent - Test: Scrape Phase Only
echo =============================================
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase scrape
echo.
pause
