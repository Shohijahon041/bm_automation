@echo off
chcp 65001 >nul
rem Dashboard orqa fonda (oynasiz) ishlaydi - pythonw.exe
cd /d "C:\Users\User\OneDrive\7496~1\DEFAUL~1"
"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" -X utf8 -u -m bm_automation dashboard --port 8080 --no-browser >> "C:\Users\User\OneDrive\7496~1\DEFAUL~1\state\dashboard_console.log" 2>&1
