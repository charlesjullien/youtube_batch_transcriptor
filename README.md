# Transcriptions de playlists YouTube

Application locale : à partir du lien d’une playlist ou d’une seule vidéo, elle récupère les sous-titres YouTube (piste manuelle de préférence, sinon automatique ; français d’abord) et les découpe en passages de cinq secondes.

Les vidéos sans sous-titres sont ignorées. Le dossier dans `data/` porte le nom de la playlist, ou le titre de la vidéo s’il n’y en a qu’une. Chaque vidéo y a un fichier `.txt` et un fichier `.json` à son nom.

## Lancement

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Ouvrir [http://127.0.0.1:5000](http://127.0.0.1:5000), coller le lien de la playlist, puis cliquer sur **Extraire les sous-titres**.

Si les fichiers existent déjà, la vidéo n’est pas retéléchargée. **Forcer la réextraction** repart de zéro.

Dans la page de lecture, le titre est en grand, le texte en dessous, et l’horodatage à gauche de chaque ligne ouvre la vidéo à cette seconde. Les mots-clés (séparés par des virgules) sont surlignés. L’option **Masquer les passages sans mot-clé** cache le reste du texte.
