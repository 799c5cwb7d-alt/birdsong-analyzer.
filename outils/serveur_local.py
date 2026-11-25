#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Birdsong Analyzer — l'appli sur ton ordinateur, sans Internet.

Lancé par « lancer-birdsong.bat » (Windows) ou « lancer-birdsong.command » (Mac).
  1. récupère la dernière version depuis GitHub si le dossier est un clone git (sinon, rien) ;
  2. sert le dossier de l'appli sur http://localhost:8642 (cet ordinateur seulement) ;
  3. ouvre le navigateur.
Le port est toujours le même : les données gardées par le navigateur (saisies, base de sons, réglages)
sont liées à cette adresse et restent d'un lancement à l'autre.
Les résultats de la collecte (donnees-ouvertes/birdsong-opendata.json) sont chargés automatiquement par l'appli.
"""
import functools
import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

PORT = 8642
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADRESSE = f'http://localhost:{PORT}/'


def maj_depuis_github():
    if not os.path.isdir(os.path.join(RACINE, '.git')) or not shutil.which('git'):
        return
    try:
        r = subprocess.run(['git', '-C', RACINE, 'pull', '--ff-only', '--quiet'], capture_output=True, text=True, timeout=60)
        print('Appli à jour depuis GitHub.' if r.returncode == 0 else 'Mise à jour depuis GitHub impossible pour l’instant (on garde la version présente).')
    except Exception:
        print('Pas de mise à jour (pas de réseau ?) : on garde la version présente.')


class Gestion(http.server.SimpleHTTPRequestHandler):
    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                          **{'.webmanifest': 'application/manifest+json', '.mjs': 'text/javascript', '.json': 'application/json'})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')          # une nouvelle version ou de nouveaux résultats sont vus tout de suite
        super().end_headers()

    def log_message(self, *a):
        pass


def port_occupe():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def main():
    sans_nav = '--sans-navigateur' in sys.argv
    if port_occupe():
        print(f'L’appli tourne déjà : {ADRESSE}')
        if not sans_nav:
            webbrowser.open(ADRESSE)
        return
    maj_depuis_github()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), functools.partial(Gestion, directory=RACINE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'Birdsong Analyzer tourne sur {ADRESSE}  (laisse cette fenêtre ouverte ; ferme-la pour arrêter)')
    if not sans_nav:
        webbrowser.open(ADRESSE)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == '__main__':
    main()
