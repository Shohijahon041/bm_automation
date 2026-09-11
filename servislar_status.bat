@echo off
chcp 65001 >nul
title Servislar holati (STATUS)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0servislar.ps1" -Action status
echo.
pause
