@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); & '%~dp0scripts\server.ps1' start"
if errorlevel 1 pause
