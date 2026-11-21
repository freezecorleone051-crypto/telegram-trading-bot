"""
Configuration centrale du bot — lit le fichier .env à la racine du projet.

Toutes les valeurs réglables sans toucher au code sont ici (token Telegram,
seuils de signal, scan automatique, gestion du risque...). Le catalogue des
marchés, lui, est dans data/markets.py.

Toutes les lectures sont tolérantes : une valeur mal écrite dans le .env
n'empêche pas le bot de démarrer, elle retombe sur la valeur par défaut
(et un avertissement est consigné dans `CONFIG_WARNINGS`).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Racine du projet : .../telegram-trading-bot/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CONFIG_WARNINGS: list = []


def _num(name: str, default, cast=float):
    """Lit une variable numérique ; en cas d'erreur, valeur par défaut."""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return cast(raw.replace(",", "."))
    except ValueError:
        CONFIG_WARNINGS.append(f"{name}={raw!r} invalide -> défaut {default}")
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "oui", "on")


# ------------------------------------------------------------------
# Telegram
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Ton ID Telegram numérique (via @userinfobot). Seul cet ID peut utiliser le bot.
ADMIN_ID = _num("ADMIN_ID", 0, int)
# IDs supplémentaires autorisés (optionnel, séparés par des virgules)
ALLOWED_IDS = set()
for _part in os.getenv("ALLOWED_IDS", "").split(","):
    if _part.strip().isdigit():
        ALLOWED_IDS.add(int(_part.strip()))

# ------------------------------------------------------------------
# Décision de signal
# ------------------------------------------------------------------
# Le moteur compte des points haussiers et baissiers (14 au maximum par côté,
# voir core/signal_engine.py). Un signal n'est annoncé que si :
#   points du côté dominant >= SIGNAL_MIN_POINTS
#   écart entre les deux côtés >= SIGNAL_MIN_MARGIN
#   accord des indicateurs (dominant / total des points) >= SIGNAL_MIN_CONFIDENCE
SIGNAL_MIN_POINTS = _num("SIGNAL_MIN_POINTS", 7, int)
SIGNAL_MIN_MARGIN = _num("SIGNAL_MIN_MARGIN", 4, int)
SIGNAL_MIN_CONFIDENCE = _num("SIGNAL_MIN_CONFIDENCE", 70, int)

# Filtres de qualité (un signal qui les viole devient ATTENDRE)
# - marché trop calme : TP/SL mangés par le spread
MIN_ATR_PCT = _num("MIN_ATR_PCT", 0.02, float)
# - prix trop étiré (poursuivre un mouvement déjà fait) : distance à l'EMA21 en ATR.
#   0 = désactivé (défaut). Mesuré sur données simulées, ce filtre coupait environ
#   la moitié du gain en marché tendanciel : à n'activer (ex. 2.5) qu'après avoir
#   vérifié avec backtest.py qu'il aide sur TON actif.
MAX_STRETCH_ATR = _num("MAX_STRETCH_ATR", 0.0, float)
# - ADX à partir duquel le marché est considéré "en tendance"
ADX_TREND = _num("ADX_TREND", 25.0, float)

# Filtre multi-timeframe : off | soft | strict
#   strict : un signal contre la tendance du timeframe supérieur devient ATTENDRE
#   soft   : le signal est gardé mais marqué fragile
#   off    : aucun filtre (info seulement)
MTF_FILTER = os.getenv("MTF_FILTER", "").strip().lower() or "strict"   # vide = défaut, sans avertissement
if MTF_FILTER not in ("off", "soft", "strict"):
    CONFIG_WARNINGS.append(f"MTF_FILTER={MTF_FILTER!r} invalide -> strict")
    MTF_FILTER = "strict"

# ------------------------------------------------------------------
# Scan automatique
# ------------------------------------------------------------------
SCAN_INTERVAL_MIN = max(1, _num("SCAN_INTERVAL_MIN", 5, int))
COOLDOWN_MIN = _num("COOLDOWN_MIN", 15, int)
# Démarrer le scan automatiquement au lancement du bot (sans taper /scan on)
# AUTO_SCAN : ancien nom, encore accepté (anciens workflows GitHub)
SCAN_AUTOSTART = _bool("SCAN_AUTOSTART", _bool("AUTO_SCAN", False))
# Nombre de requêtes de cours en parallèle pendant un scan
SCAN_CONCURRENCY = max(1, _num("SCAN_CONCURRENCY", 4, int))

# Watchlist par défaut (modifiable en direct avec /watch add / /watch remove)
WATCHLIST = [
    ("crypto", "BTC/USDT", "15m"),
    ("stocks", "NVDA", "1h"),
    ("forex", "EUR/USD", "15m"),
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
BALANCE = _num("BALANCE", 0.0, float)
# Pourcentage du capital risqué par trade (pour calculer la quantité).
RISK_PCT = _num("RISK_PCT", 1.0, float)
# Levier maximal utilisé pour plafonner la taille de position (1 = sans levier).
LEVERAGE = max(1.0, _num("LEVERAGE", 1.0, float))
# TP = entrée + TP_ATR x ATR ; SL = entrée - SL_ATR x ATR
TP_ATR = _num("TP_ATR", 1.5, float)
SL_ATR = _num("SL_ATR", 1.0, float)

# ------------------------------------------------------------------
# Divers
# ------------------------------------------------------------------
TZ_NAME = os.getenv("TZ_NAME", "Africa/Lubumbashi").strip() or "Africa/Lubumbashi"
NB_CANDLES_CHART = _num("NB_CANDLES_CHART", 80, int)
# Durée du cache des cours en secondes (évite de marteler les API).
FEED_CACHE_TTL = _num("FEED_CACHE_TTL", 25, int)

DATA_DIR = PROJECT_ROOT / "data_store"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DISCLAIMER = ("Avertissement : outil éducatif d'analyse technique. "
              "Ceci n'est pas un conseil financier. Aucun signal n'est garanti : "
              "le trading comporte un risque de perte. Gère ton risque.")
