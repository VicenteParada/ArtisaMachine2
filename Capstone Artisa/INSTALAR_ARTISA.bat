@echo off
rem Instala Artisa en este equipo Windows con un solo comando (doble clic o INSTALAR_ARTISA.bat).
rem Pide permisos de administrador, instala WSL2 + Ubuntu + Docker, levanta Artisa, carga los
rem datos y deja el arranque automatico. Se puede repetir: no borra datos.
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Se necesitan permisos de administrador. Acepta el aviso de Windows...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\instalar.ps1" %*
