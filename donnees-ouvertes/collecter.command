#!/bin/bash
# Birdsong Analyzer — collecte et mesure des sons d'oiseaux de France (double-clic). Ctrl+C pour arrêter, relancer pour reprendre.
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Python est introuvable : installe-le depuis https://www.python.org/downloads/ puis relance."; read -r; exit 1; fi
"$PY" -c "import numpy, scipy" 2>/dev/null || { echo "Installation de numpy et scipy…"; "$PY" -m pip install --user numpy scipy || { read -r; exit 1; }; }
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg; else echo "Installe ffmpeg (https://ffmpeg.org/download.html ou « brew install ffmpeg ») puis relance."; read -r; exit 1; fi
fi
"$PY" birdsong_donnees_ouvertes.py --dossier "$HOME/birdsong-donnees" --copie-vers "$(pwd)" --git "$@"
read -r -p "Terminé — Entrée pour fermer."
