"""
Configuration centrale du bot — lit le fichier .env à la racine du projet.

Toutes les valeurs réglables sans toucher au code sont ici (token Telegram,
seuils de signal, scan automatique, gestion du risque...). Le catalogue des
marchés, lui, est dans data/markets.py.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Racine du projet : .../telegram-trading-bot/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ------------------------------------------------------------------
# Telegram
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Ton ID Telegram numérique (via @userinfobot). Seul cet ID peut utiliser le bot.
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
# IDs supplémentaires autorisés (optionnel, séparés par des virgules)
_extra = os.getenv("ALLOWED_IDS", "").strip()
ALLOWED_IDS = set()
if _extra:
    for part in _extra.split(","):
        if part.strip().isdigit():
            ALLOWED_IDS.add(int(part.strip()))

# ------------------------------------------------------------------
# Décision de signal
# ------------------------------------------------------------------
# Le moteur compte des points haussiers et baissiers (maximum ~12 de chaque
# côté avec tous les modules : EMA, croisement, RSI, MACD, Bollinger,
# Stochastique, chandeliers).
SIGNAL_MIN_CONFIDENCE = int(os.getenv("SIGNAL_MIN_CONFIDENCE", "62"))
SIGNAL_MIN_POINTS = int(os.getenv("SIGNAL_MIN_POINTS", "5"))
SIGNAL_MIN_MARGIN = int(os.getenv("SIGNAL_MIN_MARGIN", "2"))

# ------------------------------------------------------------------
# Scan automatique
# ------------------------------------------------------------------
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MIN", "5"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "15"))
AUTO_SCAN = os.getenv("AUTO_SCAN", "0").strip().lower() in {
    "1", "true", "yes", "on"
}

# Watchlist par défaut (modifiable en direct avec /watch add / /watch remove)
WATCHLIST = [
    ("crypto", "BTC/USDT", "5m"),
    ("forex", "EUR/USD", "5m"),
    ("otc", "BTC/USD OTC", "5m"),
]
_w = os.getenv("WATCHLIST", "").strip()
if _w:
    parsed = []
    for item in _w.split(","):
        parts = item.strip().split(":")
        if len(parts) == 3:
            parsed.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    if parsed:
        WATCHLIST = parsed

# ------------------------------------------------------------------
# Gestion du risque
# ------------------------------------------------------------------
# Balance fictive de calcul (0 = ne pas afficher de suggestion de position).
BALANCE = float(os.getenv("BALANCE", "0") or 0)
# Pourcentage du capital risqué par trade (pour calculer la quantité).
RISK_PCT = float(os.getenv("RISK_PCT", "1.0") or 1.0)
# TP = entrée + TP_ATR x ATR ; SL = entrée - SL_ATR x ATR
TP_ATR = float(os.getenv("TP_ATR", "1.5") or 1.5)
SL_ATR = float(os.getenv("SL_ATR", "1.0") or 1.0)

# ------------------------------------------------------------------
# Divers
# ------------------------------------------------------------------
TZ_NAME = os.getenv("TZ_NAME", "Africa/Lubumbashi")
NB_CANDLES_CHART = int(os.getenv("NB_CANDLES_CHART", "80"))
# Durée du cache des cours en secondes (évite de marteler les API).
FEED_CACHE_TTL = int(os.getenv("FEED_CACHE_TTL", "25"))

DATA_DIR = PROJECT_ROOT / "data_store"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DISCLAIMER = ("Avertissement : outil éducatif d'analyse technique. "
              "Ceci n'est pas un conseil financier. Le trading comporte un risque "
              "de perte. Gère ton risque.")
