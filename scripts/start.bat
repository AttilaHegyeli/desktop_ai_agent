@echo off
cd /d "%~dp0.."
python -m app
if errorlevel 1 pause
