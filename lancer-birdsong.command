#!/bin/bash
# Birdsong Analyzer — l'appli sur ce Mac (double-clic). Laisse la fenêtre ouverte ; ferme-la pour arrêter.
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Python est introuvable : installe-le depuis https://www.python.org/downloads/ puis relance."; read -r; exit 1; fi
exec "$PY" outils/serveur_local.py "$@"
