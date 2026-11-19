"""
Analyse multi-timeframe (MTF) : pour un actif donné, analyse plusieurs
unités de temps et vérifie si elles racontent la même histoire.

Un signal dont les timeframes supérieurs sont alignés est plus solide ;
une divergence MTF est signalée honnêtement (signal plus fragile).
"""
from data import feed
from data.feed import FeedError
from core.signal_engine import analyze

# Pour chaque timeframe choisi, les timeframes de contrôle (moyen et long)
MTF_MAP = {
    "1m": ["1m", "5m", "15m"],
    "5m": ["5m", "15m", "1h"],
    "15m": ["15m", "1h", "4h"],
    "1h": ["1h", "4h"],
    "4h": ["4h"],
}


def analyze_mtf(market: str, asset: str, timeframe: str) -> list:
    """
    Renvoie [(tf, direction, confidence), ...] pour le timeframe choisi et
    ses timeframes supérieurs. Les erreurs de récupération deviennent
    ('tf', 'INDISPONIBLE', 0) sans faire planter l'ensemble.
    """
    results = []
    seen = set()
    for tf in MTF_MAP.get(timeframe, [timeframe]):
        if tf in seen:
            continue
        seen.add(tf)
        try:
            df, _src = feed.fetch_candles(market, asset, tf, 250)
            sig = analyze(df)
            results.append((tf, sig["direction"], sig["confidence"]))
        except FeedError as e:
            results.append((tf, "INDISPONIBLE", 0))
    return results


def mtf_verdict(results: list, direction: str) -> str:
    """Conclusion en une phrase sur l'alignement des timeframes."""
    real = [(tf, d, c) for tf, d, c in results if d not in ("INDISPONIBLE",)]
    if not real:
        return "MTF : aucune donnée de contrôle disponible."
    others = [d for tf, d, c in real if tf != results[0][0]]
    if not others:
        return f"MTF : seul le timeframe {real[0][0]} a pu être contrôlé."
    if direction == "ATTENDRE":
        aligned_other = set(others)
        if len(aligned_other) == 1 and aligned_other != {"ATTENDRE"}:
            d = aligned_other.pop()
            return (f"MTF : pas de signal confirmé ici, mais les timeframes "
                    f"supérieurs penchent {d} — à surveiller.")
        return "MTF : aucune direction claire sur les timeframes supérieurs."
    if all(d == direction for d in others):
        return (f"MTF : tous les timeframes de contrôle sont alignés "
                f"{direction} — signal renforcé.")
    if any(d == direction for d in others):
        return (f"MTF : divergence partielle entre les timeframes "
                f"({' / '.join(others)}) — signal plus fragile, "
                "réduis la taille de position ou attends confirmation.")
    return (f"MTF : les timeframes supérieurs vont contre le signal "
            f"({' / '.join(others)}) — méfiance maximale, mieux vaut attendre.")
