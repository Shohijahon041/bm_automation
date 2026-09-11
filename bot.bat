@echo off
chcp 65001 >nul
rem Bot orqa fonda (oynasiz) ishlaydi - pythonw.exe
echo [%date% %time%] bot.bat ishga tushdi >> "C:\Users\User\AppData\Local\Temp\opencode\bot.log" 2>&1
cd /d "C:\Users\User\OneDrive\7496~1\DEFAUL~1"
"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" -X utf8 -u -m bm_automation bot --watchdog >> "C:\Users\User\AppData\Local\Temp\opencode\bot.log" 2>&1
echo [%date% %time%] bot.bat tugadi exit=%errorlevel% >> "C:\Users\User\AppData\Local\Temp\opencode\bot.log" 2>&1
