@echo off
cd /d "%~dp0"
echo Building PDF2DXF_SEKISAN...
python -m PyInstaller pdf2dxf_web.spec --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo Build FAILED
    exit /b 1
)
echo Build OK: dist\PDF2DXF_SEKISAN.exe
