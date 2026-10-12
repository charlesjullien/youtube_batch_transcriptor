#!/usr/bin/env bash
# Lance l'interface. Python 3 doit déjà être installé.
# Depuis le projet déjà cloné : ./lancer.sh
# Depuis un autre dossier : ./lancer.sh clone d'abord le dépôt.
set -euo pipefail

REPO_URL="https://github.com/charlesjullien/youtube_batch_transcriptor.git"
ADDRESS="http://127.0.0.1:5000"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/app.py" && -f "$script_dir/requirements.txt" ]]; then
  root="$script_dir"
else
  root="${1:-$PWD/youtube_batch_transcriptor}"
  if [[ ! -f "$root/app.py" ]]; then
    echo "Clonage du projet…"
    git clone "$REPO_URL" "$root"
  fi
  root="$(cd "$root" && pwd)"
fi

cd "$root"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 est introuvable. Installez-le en suivant le README, puis relancez ./lancer.sh"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Création de l'environnement…"
  if ! python3 -m venv .venv; then
    echo "Impossible de créer l'environnement. Vérifiez l'installation de Python 3 dans le README."
    exit 1
  fi
fi

echo "Installation des dépendances…"
.venv/bin/python -m pip install -r requirements.txt

echo "Ouverture de $ADDRESS"
if command -v xdg-open >/dev/null 2>&1; then
  (sleep 1 && xdg-open "$ADDRESS" >/dev/null 2>&1) &
elif command -v open >/dev/null 2>&1; then
  (sleep 1 && open "$ADDRESS" >/dev/null 2>&1) &
else
  echo "Ouvrez $ADDRESS dans votre navigateur."
fi

exec .venv/bin/python app.py
