# Birdsong Analyzer

Accès réservé : la page demande un profil et un mot de passe.

- `index.html` — page de connexion ; l'appli y est chiffrée et ne s'ouvre qu'avec un profil.
- `activer-mises-a-jour.html` — à faire une seule fois : crée le code qui permet de publier les mises à jour
  sans connaître les mots de passe (le code ne contient rien de secret).
- `outils/publier-mise-a-jour.mjs` — publie une nouvelle version de l'appli dans `index.html`
  (`node outils/publier-mise-a-jour.mjs appli.html index.html`) ; profils, mots de passe et données ne changent pas.
- `donnees-ouvertes/` — script de mesure des sons d'oiseaux de France en données ouvertes et ses résultats.

Les données de chaque utilisateur (saisies, base de sons, réglages) restent dans son navigateur :
une mise à jour de l'appli ne les efface pas.
