@echo off
chcp 65001 >nul
title Servislarni qayta ishga tushirish (RESTART)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0servislar.ps1" -Action restart
echo.
pause
