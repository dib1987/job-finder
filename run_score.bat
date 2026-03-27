@echo off
cd /d "c:\Agentic Workflow\Job Finder"
echo.
echo =============================================
echo  Job Agent - Step 1: Parse Resume
echo =============================================
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase parse
echo.
echo =============================================
echo  Job Agent - Step 2: Score 25 Jobs vs Resume
echo =============================================
echo (This calls Claude AI for each job - takes ~2-3 mins)
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase score
echo.
pause
