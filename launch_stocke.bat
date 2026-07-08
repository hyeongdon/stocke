@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Stocke 서버 시작
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_desktop.ps1"
if errorlevel 1 pause
