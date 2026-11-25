@echo off
chcp 65001 >nul
title Birdsong Analyzer
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/
  echo en cochant "Add python.exe to PATH", puis relance ce fichier.
  pause
  exit /b 1
)
python outils\serveur_local.py %*
if errorlevel 1 pause
