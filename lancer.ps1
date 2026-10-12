# Lance l'interface. Python 3 doit déjà être installé.
# Depuis le projet déjà cloné :
#   powershell -ExecutionPolicy Bypass -File .\lancer.ps1
# Depuis un autre dossier, le script clone d'abord le dépôt.

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/charlesjullien/youtube_batch_transcriptor.git"
$Address = "http://127.0.0.1:5000"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Test-Path (Join-Path $scriptDir "app.py")) -and (Test-Path (Join-Path $scriptDir "requirements.txt"))) {
    $root = $scriptDir
} else {
    $root = Join-Path (Get-Location) "youtube_batch_transcriptor"
    if (-not (Test-Path (Join-Path $root "app.py"))) {
        Write-Host "Clonage du projet…"
        git clone $RepoUrl $root
    }
}

Set-Location $root

$python = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    Write-Host "Python 3 est introuvable. Installez-le en suivant le README, puis relancez ce script."
    exit 1
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Création de l'environnement…"
    & $python -m venv .venv
    if (-not (Test-Path $venvPython)) {
        Write-Host "Impossible de créer l'environnement. Vérifiez l'installation de Python 3 dans le README."
        exit 1
    }
}

Write-Host "Installation des dépendances…"
& $venvPython -m pip install -r requirements.txt

Write-Host "Ouverture de $Address"
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "Start-Sleep -Seconds 1; Start-Process '$Address'"
& $venvPython app.py
