@echo off
REM Lance le bot sous Windows dans un environnement virtuel propre.
cd /d "%~dp0\.."

if not exist .env (
    echo ERREUR : fichier .env manquant. Copie .env.example en .env puis remplis-le.
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/3] Creation de l'environnement virtuel...
    python -m venv .venv
)

echo [2/3] Installation des dependances...
.venv\Scripts\pip install -q -r requirements.txt

echo [3/3] Demarrage du bot (Ctrl+C pour arreter)...
.venv\Scripts\python bot.py
pause
