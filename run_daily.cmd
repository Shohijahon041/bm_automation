@echo off
rem Daily BM reports -> Telegram (run by Task Scheduler at 22:00).
rem NOTE: paths use 8.3 short names to avoid Unicode issues in cmd.exe.
set "PY=C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "WD=C:\Users\User\OneDrive\7496~1\DEFAUL~1"
set "ROUTE=dfbfbe00-38a2-4ecc-8f3b-15b790308cbc"
set "OUT=C:\Users\User\OneDrive\7496~1\DEFAUL~1\reports"
cd /d "%WD%" || exit /b 1
set "PYTHONPATH=%WD%"
set "BM_ENV=prod"
echo [%date% %time%] start >> schedule.log 2>&1
"%PY%" -m bm_automation schedule --route "%ROUTE%" --notify --once --out "%OUT%" >> schedule.log 2>&1
echo [%date% %time%] done exit=%ERRORLEVEL% >> schedule.log 2>&1
echo [%date% %time%] db sync start >> schedule.log 2>&1
"%PY%" -X utf8 -u sync_daily.py >> schedule.log 2>&1
echo [%date% %time%] db sync done exit=%ERRORLEVEL% >> schedule.log 2>&1
exit /b %ERRORLEVEL%
