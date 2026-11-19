"""Service de scan automatique : surveille la watchlist et alerte."""
import asyncio
import logging
import time

import outputs.messages as messages
from config import settings
from data import feed
from data.feed import FeedError
from services import mtf as mtf_service
from services.mtf import mtf_verdict
from storage import history
from core.signal_engine import analyze

log = logging.getLogger("tradingbot.scanner")

ARROW = {"ACHAT": "ACHAT ▲", "VENTE": "VENTE ▼", "ATTENDRE": "ATTENDRE ●"}

# cooldown en mémoire : clé "marché:actif:tf" -> timestamp du dernier envoi
_last_sent: dict = {}


async def run_scan(bot, send_to: int) -> int:
    """Un cycle de scan : analyse la watchlist, enregistre et envoie les
    signaux confirmés. Renvoie le nombre d'alertes envoyées.

    bot : instance telegram.Bot (envoi) ; send_to : chat_id cible.
    """
    # 1) mise à jour des signaux ouverts (TP/SL touchés ?)
    try:
        closed = await asyncio.to_thread(history.update_open_signals,
                                         feed.fetch_candles)
        if closed:
            log.info("Suivi : %s signal(aux) clôturé(s)", closed)
    except Exception as e:
        log.warning("Suivi des signaux impossible : %s", e)

    # 2) cooldown en mémoire (pas deux alertes identiques rapprochées)
    last_sent = _last_sent

    watch = history.get_watchlist()
    alerts = 0
    now = time.time()
    for market, asset, tf in watch:
        key = f"{market}:{asset}:{tf}"
        try:
            df, source_label = await asyncio.to_thread(feed.fetch_candles,
                                                       market, asset, tf, 250)
            signal = await asyncio.to_thread(analyze, df)
        except FeedError as e:
            log.warning("Scan %s : %s", key, e)
            continue

        if signal["direction"] == "ATTENDRE":
            continue
        if now - last_sent.get(key, 0) < settings.COOLDOWN_MIN * 60:
            continue

        # 3) contrôle multi-timeframe avant d'alerter
        try:
            mtf_results = await asyncio.to_thread(mtf_service.analyze_mtf,
                                                  market, asset, tf)
            mtf_text = mtf_verdict(mtf_results, signal["direction"])
        except Exception:
            mtf_text = None

        # 4) enregistrement + envoi
        if history.add_signal(signal, market, asset, tf, source_label):
            last_sent[key] = now
            try:
                from outputs.chart import make_chart
                png = await asyncio.to_thread(make_chart, df, signal, asset, tf,
                                              source_label)
                caption = messages.signal_caption(asset, tf, signal)
                await bot.send_photo(chat_id=send_to, photo=png,
                                     caption=caption[:1024])
                await bot.send_message(
                    chat_id=send_to,
                    text=messages.build_signal_message(market, asset, tf, signal,
                                                       source_label, mtf_text),
                    parse_mode="HTML", disable_web_page_preview=True)
                alerts += 1
                log.info("Alerte envoyée : %s %s %s %s", asset, tf,
                         signal["direction"], signal["confidence"])
            except Exception as e:  # jamais tuer le scan pour une erreur d'envoi
                log.error("Scan %s : envoi impossible (%s)", key, e)
        else:
            log.info("Signal %s déjà ouvert, pas de doublon", key)
    return alerts
