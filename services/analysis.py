"""
Pipeline d'analyse unique, utilisé par /analyse, le scan automatique et /top :

    cours (bougies fermées) -> signal -> fraîcheur -> contrôle multi-timeframe

Avant la v2, bot.py et scanner.py avaient chacun leur copie de cette chaîne,
ce qui produisait des résultats différents pour le même actif.
"""
import logging

from config import settings
from core.signal_engine import analyze
from data import feed, markets
from services import mtf as mtf_service

log = logging.getLogger("tradingbot.analysis")


def full_analysis(market: str, asset: str, timeframe: str,
                  mtf_mode: str = "always", fetch=None) -> dict:
    """Analyse complète d'un actif.

    mtf_mode : 'always'      -> contrôle MTF systématique (analyse manuelle)
               'signal_only' -> MTF seulement si un signal est détecté (scan, /top)
               'never'       -> pas de MTF
    Lève data.feed.FeedError si les cours sont indisponibles.
    """
    fetch = fetch or feed.fetch_candles
    df, source = fetch(market, asset, timeframe, feed.MAX_BARS)
    decimals = markets.decimals_for(market, asset)
    signal = analyze(df, decimals)

    st = feed.staleness(df, timeframe)
    signal["stale"] = st["stale"]
    signal["age_s"] = st["age_s"]

    mtf_results, mtf_text, mtf_status = None, None, None
    want_mtf = (mtf_mode == "always"
                or (mtf_mode == "signal_only" and signal["direction"] != "ATTENDRE"))
    if want_mtf:
        try:
            mtf_results = mtf_service.analyze_mtf(market, asset, timeframe, fetch)
            verdict = mtf_service.assess(mtf_results, signal["direction"])
            mtf_text, mtf_status = verdict["text"], verdict["status"]
            if signal["direction"] != "ATTENDRE":
                # Le filtre strict ne vaut que pour les signaux de TENDANCE : un
                # retour à la moyenne (range) va par nature contre le mouvement court.
                if verdict["blocks"] and signal.get("regime") == "tendance":
                    signal["raw_direction"] = signal["direction"]
                    signal["direction"] = "ATTENDRE"
                    signal["blocked_by"] = "filtre multi-timeframe : " + verdict["text"]
                    signal["reasons"].append(
                        f"FILTRE : signal {signal['raw_direction']} annulé — {verdict['text']}")
                elif verdict["status"] in ("partial", "against") and settings.MTF_FILTER != "off":
                    signal["reasons"].append("ATTENTION : " + verdict["text"])
        except Exception as e:      # le MTF ne doit jamais casser l'analyse
            log.warning("MTF %s %s : %s", asset, timeframe, e)

    return {"market": market, "asset": asset, "tf": timeframe, "df": df,
            "signal": signal, "source": source, "stale": st["stale"],
            "age_s": st["age_s"], "mtf": mtf_results, "mtf_text": mtf_text,
            "mtf_status": mtf_status}
