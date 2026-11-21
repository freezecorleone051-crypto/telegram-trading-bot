"""Petits utilitaires partagés (heure locale, formats)."""
import math
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = KeyError

from config import settings


def now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.TZ_NAME))
    except (ZoneInfoNotFoundError, TypeError):
        return datetime.now(timezone.utc)


def time_str() -> str:
    return now_local().strftime("%H:%M")


def fmt_age(seconds: float) -> str:
    """Durée lisible : 12 min, 3 h 20, 2 j 4 h."""
    seconds = max(float(seconds), 0.0)
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60):02d}"
    return f"{int(seconds // 86400)} j {int(seconds % 86400 // 3600)} h"


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance à 95 % d'un taux de réussite (méthode de Wilson)."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) * z / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
