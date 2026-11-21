"""Service de scan automatique : surveille la watchlist, suit les résultats, alerte.

Un cycle de scan :
  1. met à jour les signaux ouverts (TP / SL touchés ?) et notifie les résultats ;
  2. analyse chaque élément de la watchlist (en parallèle, nombre limité) sur
     des bougies FERMÉES, avec contrôle multi-timeframe si un signal apparaît ;
  3. n'alerte que pour un signal ACHAT/VENTE confirmé, sur données fraîches,
     hors période de refroidissement, jamais deux fois pour la même bougie.

`rank_opportunities` sert aussi à la commande /top (scan d'une famille de marché).
"""
import asyncio
import logging
import time

import outputs.messages as messages
from config import settings
from core.signal_engine import InsufficientData
from data import feed, markets
from data.feed import FeedError
from services.analysis import full_analysis
from storage import history

log = logging.getLogger("tradingbot.scanner")

# cooldown en mémoire : clé "marché:actif:tf" -> timestamp du dernier envoi
_last_sent: dict = {}


async def _analyze_safe(market, asset, tf, sem, mtf_mode="signal_only"):
    """Analyse un actif ; renvoie (résultat | None, erreur | None)."""
    async with sem:
        try:
            res = await asyncio.to_thread(full_analysis, market, asset, tf, mtf_mode)
            return res, None
        except (FeedError, InsufficientData) as e:
            return None, str(e)
        except Exception as e:            # jamais tuer le scan pour un seul actif
            log.exception("Analyse %s %s %s", market, asset, tf)
            return None, f"{type(e).__name__}: {e}"


async def run_scan(bot, send_to: int) -> int:
    """Un cycle de scan. Renvoie le nombre d'alertes envoyées."""
    # 1) suivi des signaux ouverts + notification des résultats
    try:
        closed = await asyncio.to_thread(history.update_open_signals, feed.fetch_candles)
        for sig in closed:
            log.info("Suivi : %s %s %s -> %s (%+.2f R)", sig["asset"], sig["tf"],
                     sig["direction"], sig["status"], sig.get("r", 0))
            if sig["status"] in ("win", "loss", "expired"):
                try:
                    await bot.send_message(chat_id=send_to, text=messages.build_result_message(sig),
                                           parse_mode="HTML")
                except Exception as e:
                    log.warning("Résultat non envoyé : %s", e)
    except Exception as e:
        log.warning("Suivi des signaux impossible : %s", e)

    # 2) analyse en parallèle de la watchlist
    watch = history.get_watchlist()
    sem = asyncio.Semaphore(settings.SCAN_CONCURRENCY)
    outcomes = await asyncio.gather(*[
        _analyze_safe(m, a, tf, sem) for m, a, tf in watch])

    alerts = 0
    now = time.time()
    for (market, asset, tf), (res, err) in zip(watch, outcomes):
        key = f"{market}:{asset}:{tf}"
        if res is None:
            log.warning("Scan %s : %s", key, err)
            continue
        signal = res["signal"]
        if signal["direction"] == "ATTENDRE":
            continue
        if res["stale"]:
            log.info("Scan %s : données anciennes (marché fermé ?), pas d'alerte", key)
            continue
        if now - _last_sent.get(key, 0) < settings.COOLDOWN_MIN * 60:
            continue

        # 3) enregistrement (anti-doublon par bougie) puis envoi
        if not history.add_signal(signal, market, asset, tf, res["source"], origin="scan"):
            log.info("Signal %s déjà enregistré (ouvert ou même bougie)", key)
            continue
        _last_sent[key] = now
        try:
            from outputs.chart import make_chart
            png = await asyncio.to_thread(
                make_chart, res["df"], signal, asset, tf, res["source"],
                _label(market, asset), markets.decimals_for(market, asset))
            await bot.send_photo(chat_id=send_to, photo=png,
                                 caption=messages.signal_caption(asset, tf, signal)[:1024])
            await bot.send_message(
                chat_id=send_to,
                text=messages.build_signal_message(market, asset, tf, signal, res["source"],
                                                   res["mtf_text"], res["stale"], res["age_s"]),
                parse_mode="HTML", disable_web_page_preview=True)
            alerts += 1
            log.info("Alerte envoyée : %s %s %s score %s/%s", asset, tf,
                     signal["direction"], signal["score"], signal["max_points"])
        except Exception as e:      # jamais tuer le scan pour une erreur d'envoi
            log.error("Scan %s : envoi impossible (%s)", key, e)
    return alerts


def _label(market: str, asset: str) -> str:
    a = markets.get_asset(market, asset)
    return a.label if a else ""


async def rank_opportunities(market: str, timeframe: str, concurrency: int | None = None):
    """Analyse tous les actifs d'une famille (commande /top).

    Renvoie (classés, sans_signal, erreurs, nb_ignorés_marché_fermé).
    `classés` est trié par score décroissant ; rien n'est enregistré dans l'historique."""
    sem = asyncio.Semaphore(concurrency or settings.SCAN_CONCURRENCY)
    names = markets.assets_for(market)
    outcomes = await asyncio.gather(*[
        _analyze_safe(market, a, timeframe, sem) for a in names])
    ranked, no_signal, errors, stale = [], [], [], 0
    for name, (res, err) in zip(names, outcomes):
        if res is None:
            errors.append(name)
            continue
        if res["stale"]:
            stale += 1
            continue
        if res["signal"]["direction"] == "ATTENDRE":
            no_signal.append(name)
        else:
            ranked.append({"asset": name, "signal": res["signal"],
                           "mtf_status": res["mtf_status"]})
    ranked.sort(key=lambda r: (-r["signal"]["score"], -r["signal"]["confidence"]))
    return ranked, no_signal, errors, stale
