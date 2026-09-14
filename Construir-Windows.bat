@echo off
cd /d "%~dp0"
title Mishop Monitor - Armador local (Windows)
python --version >nul 2>nul
if errorlevel 1 (
  echo No encontre Python. Instalalo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH" y vuelve a abrir este archivo.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install pystray pillow pyinstaller
python -m PyInstaller --onefile --noconsole --name "MishopMonitor" --hidden-import pystray._win32 --distpath "%~dp0Instalador" --workpath "%~dp0_tmp" --specpath "%~dp0_tmp" mishop_monitor.py
rmdir /s /q "%~dp0_tmp" >nul 2>nul
if exist "Instalador\MishopMonitor.exe" (echo LISTO: Instalador\MishopMonitor.exe) else (echo ERROR: no se genero el .exe)
pause
