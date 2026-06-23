@echo off
cd /d "%~dp0"
python "%~dp0pdf2dxf.py"
if errorlevel 1 pause
