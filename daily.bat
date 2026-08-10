@echo off
chcp 65001 >nul
cd /d "C:\Users\User\OneDrive\7496~1\DEFAUL~1"
"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" -X utf8 -u -m bm_automation daily --trigger scheduled >> "C:\Users\User\AppData\Local\Temp\opencode\daily.log" 2>&1
