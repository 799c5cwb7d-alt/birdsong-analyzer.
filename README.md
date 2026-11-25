# Birdsong Analyzer

Accès réservé : la page demande un profil et un mot de passe.

## En ligne
https://799c5cwb7d-alt.github.io/birdsong-analyzer./ — nécessite GitHub Pages actif
(Settings → Pages → Source « Deploy from a branch » → branche `main`, dossier `/ (root)` → Save).

## Sur ton ordinateur
1. Récupère le dépôt avec **GitHub Desktop** (File → Clone repository → `birdsong-analyzer.`) : l'appli se met
   alors à jour toute seule et la collecte peut envoyer ses résultats. (Code → Download ZIP marche aussi, sans ces deux avantages.)
2. Double-clic sur **`lancer-birdsong.bat`** (Windows) ou **`lancer-birdsong.command`** (Mac) : l'appli s'ouvre sur
   http://localhost:8642, avec les mêmes profils et mots de passe. Laisse la fenêtre ouverte pendant l'utilisation.

Le navigateur range les données par adresse : ce qui est saisi en ligne et en local reste séparé.
Les observations (feuille Saisie) se retrouvent partout grâce à la synchronisation ⇄ ; pour ta base de sons,
📚 Références → ⬇ Sauvegarder (JSON) d'un côté, puis ⬆ Charger de l'autre.

## Données ouvertes (sons d'oiseaux de France)
Double-clic sur **`donnees-ouvertes/collecter.bat`** (ou `collecter.command` sur Mac) : installe ce qu'il faut,
télécharge les sons par lots de 20 Go, les mesure, supprime les sons, et range les résultats dans `donnees-ouvertes/`
(l'appli locale les charge aussitôt) puis les envoie sur GitHub pour la version en ligne. Arrêt : Ctrl+C ; relancer reprend.

## Fichiers
- `index.html` — page de connexion ; l'appli y est chiffrée.
- `outils/publier-mise-a-jour.mjs` — publie une nouvelle version sans connaître les mots de passe.
- `outils/serveur_local.py` — utilisé par les lanceurs.

Une mise à jour de l'appli n'efface jamais les données des utilisateurs (saisies, base de sons, réglages).
