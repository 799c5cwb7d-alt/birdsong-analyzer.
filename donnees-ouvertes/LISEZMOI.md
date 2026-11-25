# Données ouvertes — sons d'oiseaux de France

`birdsong_donnees_ouvertes.py` télécharge par lots (20 Go au plus) les sons d'oiseaux de France publiés sur
iNaturalist et xeno-canto (via GBIF), les mesure avec la même méthode que l'appli, écrit les résultats,
supprime les sons, puis passe au lot suivant. Les résultats s'additionnent d'un lot à l'autre.

Installation (une fois) : Python 3.9+, puis `python -m pip install numpy scipy`, et ffmpeg (`winget install ffmpeg`).

```
python birdsong_donnees_ouvertes.py --dossier D:\birdsong-donnees --essai 10
python birdsong_donnees_ouvertes.py --dossier D:\birdsong-donnees --copie-vers "C:\...\birdsong-analyzer.\donnees-ouvertes"
```

Le fichier `birdsong-opendata.json` placé dans ce dossier est chargé automatiquement par l'appli
(feuille 🌍 Données ouvertes) pour tous les profils. Il ne contient que des mesures ; les sons restent
la propriété de leurs auteurs (licences Creative Commons, auteur et lien de chaque son dans le tableau CSV).
