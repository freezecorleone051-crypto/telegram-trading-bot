#!/usr/bin/env bash
# Lance le bot en environnement virtuel propre (Linux / macOS).
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "ERREUR : fichier .env manquant. Copie .env.example puis remplis-le :"
    echo "  cp .env.example .env"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "[1/3] Creation de l'environnement virtuel..."
    python3 -m venv .venv
fi

echo "[2/3] Installation des dependances..."
.venv/bin/pip install -q -r requirements.txt

echo "[3/3] Demarrage du bot (Ctrl+C pour arreter)..."
exec .venv/bin/python bot.py
