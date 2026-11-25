# Birdsong Analyzer

Accès réservé : la page demande un profil et un mot de passe.

- `index.html` — page de connexion ; l'appli y est chiffrée et ne s'ouvre qu'avec un profil.
- `outils/publier-mise-a-jour.mjs` — publie une nouvelle version de l'appli dans `index.html`
  (`node outils/publier-mise-a-jour.mjs appli.html index.html`) ; profils, mots de passe et données ne changent pas.
- `donnees-ouvertes/` — script de mesure des sons d'oiseaux de France en données ouvertes et ses résultats.

Les données de chaque utilisateur (saisies, base de sons, réglages) restent dans son navigateur :
une mise à jour de l'appli ne les efface pas.
