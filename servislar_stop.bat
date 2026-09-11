@echo off
chcp 65001 >nul
title Servislarni ochirish (STOP)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0servislar.ps1" -Action stop
echo.
pause
