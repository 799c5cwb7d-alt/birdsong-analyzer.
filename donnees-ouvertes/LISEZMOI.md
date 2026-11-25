# Données ouvertes — sons d'oiseaux de France

**Lancer :** double-clic sur `collecter.bat` (Windows) ou `collecter.command` (Mac).
La première fois, il installe numpy, scipy et ffmpeg (sous Windows, relance-le une fois après l'installation de ffmpeg).

Ce qu'il fait, en boucle jusqu'à la fin : inventaire des sons d'oiseaux de France publiés sur iNaturalist et
xeno-canto (via GBIF) → téléchargement d'un lot (20 Go au plus) → mesure avec la même méthode que l'appli →
résultats dans ce dossier (`birdsong-opendata.json`, chargé tout seul par l'appli) et envoi sur GitHub →
suppression des sons → lot suivant. Les résultats s'additionnent d'un lot à l'autre.

- Sons, base de suivi et tableaux CSV : dossier `birdsong-donnees` de ton dossier personnel (Windows : `C:\Users\<toi>\birdsong-donnees`).
  Pour un autre disque : `collecter.bat --dossier D:\birdsong-donnees`.
- Arrêt : Ctrl+C. Relancer reprend là où c'était arrêté.
- iNaturalist limite les téléchargements (5 Go/heure, 24 Go/jour) : le script reste en dessous tout seul.
- Les sons restent la propriété de leurs auteurs (licences Creative Commons) ; seules des mesures sont gardées,
  avec l'auteur, la licence et le lien de chaque son dans `mesures-par-enregistrement.csv`.
