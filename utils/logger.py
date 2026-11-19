"""Logging rotatif : console + fichier logs/bot.log."""
import logging
from logging.handlers import RotatingFileHandler

from config import settings

_configured = False


def setup_logging() -> logging.Logger:
    """Configure le logging une seule fois et renvoie le logger racine du bot."""
    global _configured
    log = logging.getLogger("tradingbot")
    if _configured:
        return log

    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.LOG_DIR / "bot.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    # Réduire le bruit des librairies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("peewee").setLevel(logging.CRITICAL)
    _configured = True
    return log
