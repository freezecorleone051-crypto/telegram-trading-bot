"""Petits utilitaires partagés (heure locale, formats)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.TZ_NAME))


def time_str() -> str:
    return now_local().strftime("%H:%M")
