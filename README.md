<img width="2558" height="1293" alt="image" src="https://github.com/user-attachments/assets/dac15f5b-944e-49fb-8871-018c928d7174" />


# Transcriptions de playlists YouTube

Application locale : à partir du lien d’une playlist ou d’une seule vidéo, elle récupère les sous-titres YouTube (piste manuelle de préférence, sinon automatique ; français d’abord) et les découpe en passages de cinq secondes.

Les vidéos sans sous-titres sont ignorées. Le dossier dans `data/` porte le nom de la playlist, ou le titre de la vidéo s’il n’y en a qu’une. Chaque vidéo y a un fichier `.txt` et un fichier `.json` à son nom.

## Python

Installez Python 3.10 ou plus récent avant de lancer le script. Git sert seulement à cloner le dépôt.

### Windows

1. Ouvrez [python.org/downloads](https://www.python.org/downloads/) et téléchargez l’installateur Windows.
2. Lancez-le et cochez **Add python.exe to PATH**.
3. Terminez l’installation, puis fermez et rouvrez PowerShell.
4. Vérifiez avec `python --version`.

### Linux

Debian ou Ubuntu :

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

Fedora :

```bash
sudo dnf install python3
```

Arch Linux :

```bash
sudo pacman -S python
```

Vérifiez avec `python3 --version`.

## Lancement

Linux ou macOS, après avoir cloné le dépôt :

```bash
git clone https://github.com/charlesjullien/youtube_batch_transcriptor.git
cd youtube_batch_transcriptor
./lancer.sh
```

Windows, dans PowerShell :

```powershell
git clone https://github.com/charlesjullien/youtube_batch_transcriptor.git
cd youtube_batch_transcriptor
powershell -ExecutionPolicy Bypass -File .\lancer.ps1
```

Le script installe les dépendances au premier lancement, démarre l'application et ouvre [http://127.0.0.1:5000](http://127.0.0.1:5000). Collez le lien d'une playlist ou d'une vidéo, puis cliquez sur **Extraire les sous-titres**.

Si les fichiers existent déjà, la vidéo n’est pas retéléchargée. **Forcer la réextraction** repart de zéro.

Dans la page de lecture, le titre est en grand, le texte en dessous, et l’horodatage à gauche de chaque ligne ouvre la vidéo à cette seconde. Les mots-clés (séparés par des virgules) sont surlignés. L’option **Masquer les passages sans mot-clé** cache le reste du texte.
