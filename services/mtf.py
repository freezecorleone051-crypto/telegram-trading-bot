"""
Analyse multi-timeframe (MTF) : pour un actif donné, regarde le timeframe
choisi ET les timeframes supérieurs, et vérifie qu'on ne trade pas CONTRE la
tendance de fond (« ne jamais se battre contre le timeframe supérieur »).

La v1 comparait seulement les verdicts ACHAT/VENTE/ATTENDRE, si bien qu'un
timeframe supérieur simplement « neutre » était présenté comme « contre le
signal ». La v2 compare la TENDANCE de fond (EMA21 / EMA50 / prix) :
  haussière / baissière / neutre, et ne déclare « contre » que si elle est
  réellement opposée.

Modes (réglage MTF_FILTER dans .env) :
  strict : signal annulé (ATTENDRE) si le timeframe supérieur immédiat est contre
  soft   : signal gardé mais marqué fragile
  off    : information seulement
"""
import logging

from config import settings
from data import feed, markets
from data.feed import FeedError
from core.signal_engine import analyze, InsufficientData

log = logging.getLogger("tradingbot.mtf")

TREND_SIGN = {"haussière": 1, "baissière": -1, "neutre": 0}


def analyze_mtf(market: str, asset: str, timeframe: str, fetch=None) -> list:
    """Renvoie une liste de dicts, le premier étant le timeframe choisi :
    {tf, available, direction, bias, trend, regime, confidence}.
    Une erreur de récupération donne available=False sans casser l'ensemble."""
    fetch = fetch or feed.fetch_candles
    tfs = markets.mtf_map_for(market).get(timeframe, [timeframe])
    decimals = markets.decimals_for(market, asset)
    results, seen = [], set()
    for tf in tfs:
        if tf in seen:
            continue
        seen.add(tf)
        try:
            df, _src = fetch(market, asset, tf, 250)
            sig = analyze(df, decimals)
            results.append({"tf": tf, "available": True, "direction": sig["direction"],
                            "bias": sig["bias"], "trend": sig["trend"],
                            "regime": sig["regime"], "confidence": sig["confidence"]})
        except (FeedError, InsufficientData) as e:
            log.info("MTF %s %s : %s indisponible (%s)", asset, tf, tf, e)
            results.append({"tf": tf, "available": False, "direction": "INDISPONIBLE",
                            "bias": "NEUTRE", "trend": "neutre", "regime": "?",
                            "confidence": 0})
    return results


def assess(results: list, direction: str, mode: str | None = None) -> dict:
    """Conclusion sur l'alignement des timeframes.

    Renvoie {status, text, blocks} avec status parmi :
    aligned | partial | neutral | against | none.
    `blocks` = True si le mode « strict » doit annuler le signal."""
    mode = mode or settings.MTF_FILTER
    htf = [r for r in results[1:] if r["available"]]
    if not htf:
        base_ok = results and results[0]["available"]
        txt = ("MTF : aucun timeframe supérieur disponible pour contrôler."
               if base_ok else "MTF : aucune donnée de contrôle disponible.")
        return {"status": "none", "text": txt, "blocks": False}

    trends = " / ".join(f"{r['tf']} {r['trend']}" for r in htf)
    if direction == "ATTENDRE":
        signs = {TREND_SIGN[r["trend"]] for r in htf}
        if signs == {1}:
            t = "MTF : pas de signal confirmé ici, mais les timeframes supérieurs sont haussiers — à surveiller (" + trends + ")."
        elif signs == {-1}:
            t = "MTF : pas de signal confirmé ici, mais les timeframes supérieurs sont baissiers — à surveiller (" + trends + ")."
        else:
            t = f"MTF : aucune direction claire sur les timeframes supérieurs ({trends})."
        return {"status": "neutral", "text": t, "blocks": False}

    sign = 1 if direction == "ACHAT" else -1
    signs = [TREND_SIGN[r["trend"]] for r in htf]
    against = [r for r, sg in zip(htf, signs) if sg == -sign]
    same = [r for r, sg in zip(htf, signs) if sg == sign]
    immediate_against = signs[0] == -sign

    if immediate_against:
        status = "against"
        masc = {"haussière": "haussier", "baissière": "baissier"}.get(htf[0]["trend"], htf[0]["trend"])
        text = (f"MTF : le timeframe supérieur immédiat ({htf[0]['tf']}) est "
                f"{masc}, CONTRE le signal ({trends}) — on ne se bat pas "
                "contre la tendance de fond.")
    elif against:
        status = "partial"
        text = (f"MTF : divergence partielle ({trends}) — signal plus fragile, "
                "réduis la taille de position ou attends confirmation.")
    elif len(same) == len(htf):
        status = "aligned"
        text = f"MTF : tous les timeframes de contrôle sont alignés {direction} ({trends}) — signal renforcé."
    elif same:
        status = "partial"
        text = (f"MTF : alignement partiel ({trends}) — certains timeframes "
                "supérieurs sont neutres, signal à prendre avec prudence.")
    else:
        status = "neutral"
        text = f"MTF : timeframes supérieurs neutres ({trends}) — pas de renfort de tendance."
    return {"status": status, "text": text,
            "blocks": (mode == "strict" and status == "against")}
