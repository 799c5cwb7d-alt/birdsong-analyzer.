@echo off
chcp 65001 >nul
title Birdsong Analyzer - collecte des donnees ouvertes
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/
  echo en cochant "Add python.exe to PATH", puis relance ce fichier.
  pause
  exit /b 1
)
python -c "import numpy, scipy" 2>nul || (
  echo Installation de numpy et scipy...
  python -m pip install --user numpy scipy || ( echo Installation impossible. & pause & exit /b 1 )
)
where ffmpeg >nul 2>nul || (
  echo Installation de ffmpeg...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo ffmpeg est installe : ferme cette fenetre puis relance collecter.bat.
  pause
  exit /b 0
)
python birdsong_donnees_ouvertes.py --dossier "%USERPROFILE%\birdsong-donnees" --copie-vers "%~dp0." --git %*
pause
