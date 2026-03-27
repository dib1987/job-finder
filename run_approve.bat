@echo off
cd /d "c:\Agentic Workflow\Job Finder"
echo.
echo =============================================
echo  Job Agent - Approval Gate
echo =============================================
echo  Review each matched job and press:
echo    A = Add to apply list
echo    S = Skip
echo    Q = Quit (saves progress)
echo =============================================
echo.
"C:\Users\dibye\AppData\Local\Programs\Python\Python313\python.exe" agent.py --phase approve
echo.
pause
