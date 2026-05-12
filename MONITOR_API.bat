@echo off
cd /d "%~dp0"
title Monitor Prensadao - API
color 0A
echo.
echo  ================================
echo   MONITOR PRENSADAO - API
echo   Sem Chrome, sem Selenium!
echo  ================================
echo.
echo  Instalando dependencias...
pip install requests >nul 2>&1
echo  OK!
echo.
echo  Iniciando monitor...
echo  Painel: https://painel-prensadao.onrender.com
echo.
python monitor_api.py
pause
