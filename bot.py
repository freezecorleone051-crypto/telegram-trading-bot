"""
Bot Telegram d'analyse technique de trading — usage privé.

Commandes :
  /start                 menu de démarrage avec boutons
  /analyse [actif] [tf]  analyse complète + image (ex : /analyse nvidia 1h)
  /top [marché] [tf]     classe les opportunités d'une famille (ex : /top actions 1h)
  /prix [actif]          prix en direct + variation
  /marches               liste des marchés supportés
  /scan on|off           scan automatique de la watchlist
  /watch ...             gérer la watchlist (add/remove/clear)
  /stats                 résultats réels des signaux passés (en multiples de R)
  /backtest [actif] [tf] teste le moteur sur l'historique (avec coûts)
  /id                    affiche ton ID Telegram
  /help                  aide détaillée

Accès privé : seuls ADMIN_ID et (optionnel) ALLOWED_IDS peuvent l'utiliser.
"""
import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

from config import settings
from core.signal_engine import InsufficientData
from data import feed, markets
from data.feed import FeedError
from outputs import chart, messages
from services import backtest as backtest_service
from services import scanner
from services.analysis import full_analysis
from storage import history
from utils.logger import setup_logging

log = setup_logging()
for _w in settings.CONFIG_WARNINGS:
    log.warning("Configuration : %s", _w)

_scan_lock = asyncio.Lock()


# ==================================================================
# Contrôle d'accès privé
# ==================================================================
def allowed(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    return user.id == settings.ADMIN_ID or user.id in settings.ALLOWED_IDS


def private_guard(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Ce bot est privé. Il n'accepte que son propriétaire. "
                    "(Pour connaître ton ID Telegram : /id)")
            return
        return await func(update, context)
    return wrapper


def _html(text) -> str:
    return html.escape(str(text))


def _parse_asset_tf(tokens: list):
    """['nvidia', '1h'] -> ('nvidia', '1h') ; ['BTC', 'OTC'] -> ('BTC OTC', None)."""
    tokens = list(tokens)
    tf = None
    if tokens and markets.parse_timeframe(tokens[-1]):
        tf = markets.parse_timeframe(tokens.pop())
    return " ".join(tokens), tf


# ==================================================================
# Analyse complète : cours -> signal -> MTF -> image -> messages
# ==================================================================
async def run_full_analysis(market: str, asset: str, timeframe: str):
    """Chaîne complète. Renvoie (png, texte, légende) ou lève FeedError."""
    res = await asyncio.to_thread(full_analysis, market, asset, timeframe, "always")
    signal = res["signal"]
    a = markets.get_asset(market, asset)
    png = await asyncio.to_thread(
        chart.make_chart, res["df"], signal, asset, timeframe, res["source"],
        a.label if a else "", markets.decimals_for(market, asset))

    # historique : uniquement les signaux confirmés, sur données fraîches
    if signal["direction"] != "ATTENDRE" and not res["stale"]:
        added = await asyncio.to_thread(history.add_signal, signal, market, asset,
                                        timeframe, res["source"], "manual")
        if added:
            log.info("Signal enregistré : %s %s %s (score %s/%s)", asset, timeframe,
                     signal["direction"], signal["score"], signal["max_points"])

    text = messages.build_signal_message(market, asset, timeframe, signal, res["source"],
                                         res["mtf_text"], res["stale"], res["age_s"])
    return png, text, messages.signal_caption(asset, timeframe, signal)


async def send_analysis(context: ContextTypes.DEFAULT_TYPE, chat_id: int, market: str,
                        asset: str, timeframe: str) -> None:
    """Envoie l'analyse dans le chat `chat_id`.

    (v1 : on recevait l'objet bouton et on utilisait son `.id` — qui est
    l'identifiant du CLIC, pas du chat : l'analyse lancée depuis les boutons
    du menu échouait avec « Chat not found ».)"""
    status = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Analyse de {asset} en {timeframe}… (cours, régime, 8 modules, contrôle "
             "multi-timeframe, image)")
    try:
        png, text, caption = await run_full_analysis(market, asset, timeframe)
    except (FeedError, InsufficientData) as e:
        await status.edit_text(
            f"Impossible d'analyser {asset} ({timeframe}).\n"
            f"Détail : {e}\n\nCauses fréquentes : marché fermé, API momentanément "
            "indisponible, ou actif mal orthographié (voir /marches). "
            "Réessaie dans quelques instants.")
        return
    except Exception:
        log.exception("Analyse %s %s", asset, timeframe)
        await status.edit_text("Erreur interne pendant l'analyse. Elle est consignée dans "
                               "les logs ; réessaie dans un instant.")
        return

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        await context.bot.send_photo(chat_id=chat_id, photo=png, caption=caption[:1024])
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        log.warning("Envoi de l'image impossible : %s", e)
        try:
            await status.edit_text(caption[:4096])
        except Exception:
            pass
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)


# ==================================================================
# Claviers inline
# ==================================================================
def market_keyboard(prefix: str = "cat"):
    rows, row = [], []
    for m in markets.MARKET_ORDER:
        row.append(InlineKeyboardButton(markets.MARKET_INFO[m]["button"],
                                        callback_data=f"{prefix}:{m}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if prefix == "cat":
        rows.append([InlineKeyboardButton("Marchés supportés", callback_data="help:assets")])
    return InlineKeyboardMarkup(rows)


def asset_keyboard(market: str):
    cols = 2 if market in ("crypto", "forex", "otc") else 3
    rows, row = [], []
    for a in markets.assets_for(market):
        row.append(InlineKeyboardButton(a, callback_data=f"asset:{market}:{a}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Classer les opportunités (/top)",
                                      callback_data=f"top:{market}")])
    rows.append([InlineKeyboardButton("< Changer de marché", callback_data="back:cat")])
    return InlineKeyboardMarkup(rows)


def timeframe_keyboard(market: str, asset: str):
    tfs = markets.timeframes_for(market)
    rows, row = [], []
    for tf in tfs:
        row.append(InlineKeyboardButton(tf, callback_data=f"tf:{market}:{asset}:{tf}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("< Changer d'actif", callback_data=f"cat:{market}")])
    return InlineKeyboardMarkup(rows)


# ==================================================================
# Commandes
# ==================================================================
@private_guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Bienvenue ! Je suis ton assistant d'analyse technique de trading.\n\n"
        "<b>Ce que je fais :</b>\n"
        "- Marchés : crypto, <b>actions US (NVIDIA, Microsoft, Apple…)</b>, ETF, indices "
        "(S&P 500, Nasdaq, DAX…), matières premières (pétrole, cuivre, argent…), forex et or.\n"
        "- J'analyse uniquement des bougies <b>fermées</b>, je lis le régime du marché "
        "(tendance ou range) et je combine 8 modules (EMA, MACD, ADX, RSI, Bollinger, "
        "stochastique, chandeliers, volume) pour un signal ACHAT / VENTE avec entrée, "
        "Take Profit et Stop Loss — ou ATTENDRE quand il n'y a rien de propre.\n"
        "- Je contrôle le timeframe supérieur et je t'envoie l'<b>image du graphique</b>.\n"
        "- /top classe les opportunités d'une famille de marché.\n"
        "- /stats donne les résultats RÉELS de mes signaux, /backtest teste le moteur "
        "sur l'historique : vérifie avant de faire confiance.\n"
        "- /scan on surveille ta watchlist et t'alerte.\n\n"
        "<b>Commandes :</b> /analyse — /top — /prix — /marches — /scan — /watch — "
        "/stats — /backtest — /help — /id\n\n"
        "Choisis une famille de marché :")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=market_keyboard())


@private_guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Aide</b>\n"
        "/start — menu avec boutons\n"
        "/analyse [actif] [timeframe] — analyse complète\n"
        "   ex : /analyse nvidia 1h | /analyse BTC 15m | /analyse or 1h | /analyse dax 1d\n"
        "/top [marché] [timeframe] — classe les actifs d'une famille\n"
        "   ex : /top actions 1h | /top crypto 15m | /top indices 1d\n"
        "/prix [actif] — prix en direct + variation\n"
        "/marches — liste des actifs\n"
        "/scan on / /scan off — scan automatique de la watchlist\n"
        "/watch add NVDA 1h — ajouter ; /watch remove NVDA 1h — retirer ; /watch clear\n"
        "/stats — résultats réels (TP / SL / espérance en R)\n"
        "/backtest [actif] [timeframe] [nb_bougies] — test sur l'historique, coûts inclus\n"
        "/id — ton ID Telegram\n\n"
        "<b>Comment je décide :</b>\n"
        "1. Je ne lis que des bougies FERMÉES (aucun signal qui change avant la clôture).\n"
        "2. L'ADX donne le régime : en TENDANCE (ADX ≥ 25) je suis la tendance "
        "(EMA 9/21/50, MACD, RSI, Bollinger, stochastique, chandeliers, volume : "
        "14 points max) ; en RANGE je joue les retours à la moyenne (écart à l'EMA21, "
        "RSI, Bollinger, stochastique, chandeliers, volume : 10 points max).\n"
        f"3. Signal confirmé seulement si le côté dominant a ≥ {settings.SIGNAL_MIN_POINTS} "
        f"points (échelle tendance), un écart ≥ {settings.SIGNAL_MIN_MARGIN} et un accord "
        f"≥ {settings.SIGNAL_MIN_CONFIDENCE}%. Sinon : ATTENDRE.\n"
        f"4. Filtres : marché trop calme (ATR < {settings.MIN_ATR_PCT:g}%), entrée tardive "
        f"(RSI extrême), données anciennes (marché fermé), et — en tendance — signal "
        f"contre le timeframe supérieur (mode {settings.MTF_FILTER}).\n"
        f"5. TP = {settings.TP_ATR:g} x ATR, SL = {settings.SL_ATR:g} x ATR.\n\n"
        "<b>Important :</b> « accord » n'est pas une probabilité de gagner. "
        "Seuls /stats (réel) et /backtest (historique) mesurent l'efficacité.")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@private_guard
async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Quel marché veux-tu analyser ? Choisis la famille :",
                                        reply_markup=market_keyboard())
        return
    query, tf = _parse_asset_tf(context.args)
    match = markets.find_asset(query)
    if not match:
        await update.message.reply_text(
            "Actif non reconnu. Voici ce que je connais : /marches\n\n"
            "Exemples : /analyse nvidia 1h — /analyse BTC 15m — /analyse or 1h "
            "— /analyse dax 1d — /analyse BTC OTC 5m")
        return
    market, asset = match
    tf = tf or markets.default_timeframe(market)
    if tf not in markets.fetchable_timeframes(market):
        await update.message.reply_text(
            f"{asset} n'existe pas en {tf}. Timeframes disponibles : "
            + ", ".join(markets.timeframes_for(market)))
        return
    await send_analysis(context, update.effective_chat.id, market, asset, tf)


@private_guard
async def cmd_marches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(messages.build_markets_message(),
                                    parse_mode=ParseMode.HTML)


async def _run_top(context: ContextTypes.DEFAULT_TYPE, chat_id: int, market: str,
                   timeframe: str):
    n = len(markets.assets_for(market))
    status = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Analyse de {n} actifs ({markets.MARKET_INFO[market]['button']}, {timeframe})… "
             "environ 10 à 30 secondes.")
    try:
        ranked, no_signal, errors, stale = await scanner.rank_opportunities(market, timeframe)
    except Exception:
        log.exception("/top %s %s", market, timeframe)
        await status.edit_text("Erreur interne pendant le classement. Réessaie dans un instant.")
        return
    if errors and len(errors) == n:
        await status.edit_text("Aucune donnée disponible pour cette famille en ce moment "
                               "(API indisponible ?). Réessaie dans quelques minutes.")
        return
    await status.edit_text(
        messages.build_top_message(market, timeframe, ranked, no_signal, errors, stale),
        parse_mode=ParseMode.HTML)


@private_guard
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Quelle famille classer ? (ou tape /top actions 1h)",
            reply_markup=market_keyboard("top"))
        return
    market = markets.parse_market(context.args[0])
    if not market:
        await update.message.reply_text(
            "Famille inconnue. Choisis : crypto, actions, etf, indices, matieres, forex, otc.\n"
            "Exemple : /top actions 1h")
        return
    tf = (markets.parse_timeframe(context.args[1]) if len(context.args) > 1
          else markets.default_timeframe(market))
    if not tf or tf not in markets.fetchable_timeframes(market):
        await update.message.reply_text("Timeframe invalide pour cette famille. Disponibles : "
                                        + ", ".join(markets.timeframes_for(market)))
        return
    await _run_top(context, update.effective_chat.id, market, tf)


# ------------------------------------------------------------------
# Scan automatique
# ------------------------------------------------------------------
def _start_scan_job(job_queue) -> bool:
    if job_queue is None:
        return False
    if job_queue.get_jobs_by_name("scan"):
        return True
    job_queue.run_repeating(scan_job, interval=settings.SCAN_INTERVAL_MIN * 60, first=10,
                            name="scan", chat_id=settings.ADMIN_ID)
    return True


def _watch_text(watch) -> str:
    return ", ".join(f"{a} ({tf})" for _m, a, tf in watch) or "(vide)"


@private_guard
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    jobs = context.job_queue.get_jobs_by_name("scan") if context.job_queue else []
    watch = history.get_watchlist()

    if arg == "on":
        if context.job_queue is None:
            await update.message.reply_text(
                "Le planificateur n'est pas disponible. Installe : "
                "pip install \"python-telegram-bot[job-queue]\"")
            return
        if jobs:
            await update.message.reply_text(
                f"Le scan automatique est DÉJÀ actif (toutes les {settings.SCAN_INTERVAL_MIN} "
                f"min). Watchlist : {_watch_text(watch)}")
            return
        _start_scan_job(context.job_queue)
        await asyncio.to_thread(history.set_scan_enabled, True)
        await update.message.reply_text(
            f"Scan automatique ACTIVÉ : je surveille {_watch_text(watch)} toutes les "
            f"{settings.SCAN_INTERVAL_MIN} min. Je t'enverrai le message + l'image dès qu'un "
            "signal confirmé sort, puis le résultat (TP / SL) quand il tombe. Le scan "
            "reste activé après un redémarrage du bot.")
    elif arg == "off":
        for j in jobs:
            j.schedule_removal()
        await asyncio.to_thread(history.set_scan_enabled, False)
        await update.message.reply_text("Scan automatique DÉSACTIVÉ.")
    else:
        state = "actif" if jobs else "arrêté"
        await update.message.reply_text(
            f"Scan automatique : actuellement {state}.\n"
            "/scan on pour démarrer — /scan off pour arrêter.\n"
            f"Watchlist : {_watch_text(watch)}")


@private_guard
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0].lower() not in ("add", "remove", "clear", "list"):
        await update.message.reply_text(
            messages.build_watchlist_message(history.get_watchlist()),
            parse_mode=ParseMode.HTML)
        return
    action = args[0].lower()
    if action == "clear":
        await asyncio.to_thread(history.watchlist_clear)
        await update.message.reply_text("Watchlist vidée.")
        return
    if action == "list" or len(args) < 2:
        await update.message.reply_text(
            messages.build_watchlist_message(history.get_watchlist()),
            parse_mode=ParseMode.HTML)
        return

    query, tf = _parse_asset_tf(args[1:])
    match = markets.find_asset(query)
    if not match:
        await update.message.reply_text(
            "Actif non reconnu (voir /marches). Exemple : /watch add nvidia 1h")
        return
    market, asset = match
    tf = tf or markets.default_timeframe(market)
    if action == "add":
        if tf not in markets.fetchable_timeframes(market):
            await update.message.reply_text(
                f"{asset} n'existe pas en {tf}. Disponibles : "
                + ", ".join(markets.timeframes_for(market)))
            return
        ok = await asyncio.to_thread(history.watchlist_add, market, asset, tf)
        await update.message.reply_text(
            f"{asset} ({tf}) ajouté à la watchlist. Si le scan est actif (/scan on), il sera "
            "surveillé." if ok else f"{asset} ({tf}) est déjà dans la watchlist.")
    else:
        ok = await asyncio.to_thread(history.watchlist_remove, market, asset, tf)
        await update.message.reply_text(f"{asset} ({tf}) retiré." if ok
                                        else f"{asset} ({tf}) n'était pas dans la watchlist.")


@private_guard
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text(
        "Mise à jour des signaux ouverts (vérification TP/SL sur les cours)…")
    try:
        closed = await asyncio.to_thread(history.update_open_signals, feed.fetch_candles)
    except Exception as e:
        log.warning("update_open_signals : %s", e)
        closed = []
    st = await asyncio.to_thread(history.stats)
    head = (f"Mise à jour : {len(closed)} signal(aux) clôturé(s) depuis la dernière "
            "vérification.\n\n") if closed else ""
    await status.edit_text(head + messages.build_stats_message(st), parse_mode=ParseMode.HTML)


@private_guard
async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Teste le moteur sur l'historique d'un actif (coûts inclus).\n"
            "Exemples : /backtest nvidia 1h — /backtest BTC 15m 3000 — /backtest eurusd 15m\n"
            "Plus il y a de trades, plus le résultat est fiable (≥ 30 minimum).")
        return
    tokens = list(context.args)
    bars = 1500
    if tokens and tokens[-1].isdigit():
        bars = min(max(int(tokens.pop()), 300), 5000)
    query, tf = _parse_asset_tf(tokens)
    match = markets.find_asset(query)
    if not match:
        await update.message.reply_text("Actif non reconnu (voir /marches).")
        return
    market, asset = match
    tf = tf or markets.default_timeframe(market)
    if tf not in markets.fetchable_timeframes(market):
        await update.message.reply_text(f"{asset} n'existe pas en {tf}.")
        return
    status = await update.message.reply_text(
        f"Backtest de {asset} en {tf} sur ~{bars} bougies… (quelques secondes)")
    try:
        df, source = await asyncio.to_thread(feed.fetch_history, market, asset, tf, bars)
        out = await asyncio.to_thread(
            backtest_service.run_backtest, df, tf, market,
            backtest_service.DEFAULT_COST_PCT.get(market, 0.05), None, 80,
            settings.MTF_FILTER == "strict", markets.decimals_for(market, asset))
    except (FeedError, InsufficientData) as e:
        await status.edit_text(f"Backtest impossible : {e}")
        return
    except Exception:
        log.exception("/backtest %s %s", asset, tf)
        await status.edit_text("Erreur interne pendant le backtest (voir les logs).")
        return
    report = backtest_service.format_report(asset, tf, out["summary"], source)
    await status.edit_text(
        "<pre>" + _html(report) + "</pre>\n<i>" + _html(
            "Simulation sur le passé : jamais une promesse pour le futur. "
            + settings.DISCLAIMER) + "</i>", parse_mode=ParseMode.HTML)


@private_guard
async def cmd_prix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else "BTC"
    match = markets.find_asset(query)
    if not match:
        await update.message.reply_text("Actif non reconnu (voir /marches).")
        return
    market, asset = match
    try:
        info = await asyncio.to_thread(feed.fetch_price, market, asset)
    except FeedError as e:
        await update.message.reply_text(
            f"Prix indisponible pour {asset} : {e}\n(marché fermé ? API indisponible ?)")
        return
    await update.message.reply_text(
        messages.build_price_message(asset, info["price"], info["change_pct"], info["source"],
                                     info["span"], markets.decimals_for(market, asset), query),
        parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Volontairement SANS garde : il faut pouvoir lire son ID avant d'être autorisé."""
    await update.message.reply_text(
        f"Ton ID Telegram : <b>{update.effective_user.id}</b>\n"
        "Mets-le dans le fichier .env (ADMIN_ID=...) pour l'accès privé.",
        parse_mode=ParseMode.HTML)


# ==================================================================
# Boutons
# ==================================================================
async def _edit(query, text: str, **kw):
    try:
        await query.edit_message_text(text, **kw)
    except Exception as e:      # ex. « Message is not modified »
        log.debug("edit_message_text : %s", e)


@private_guard
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()
    chat_id = update.effective_chat.id

    if data == "back:cat" or data.startswith("cat:"):
        market = data.split(":", 1)[1] if ":" in data else ""
        if market in markets.MARKET_INFO:
            await _edit(query, f"Marché : {markets.MARKET_LABELS[market]}\nChoisis l'actif :",
                        reply_markup=asset_keyboard(market))
        else:
            await _edit(query, "Quelle famille de marché ?", reply_markup=market_keyboard())
    elif data.startswith("asset:"):
        _, market, asset = data.split(":", 2)
        await _edit(query, f"{asset} — choisis le timeframe :",
                    reply_markup=timeframe_keyboard(market, asset))
    elif data.startswith("tf:"):
        _, market, asset, tf = data.split(":", 3)
        await send_analysis(context, chat_id, market, asset, tf)
    elif data.startswith("top:"):
        market = data.split(":", 1)[1]
        if market in markets.MARKET_INFO:
            await _run_top(context, chat_id, market, markets.default_timeframe(market))
    elif data == "help:assets":
        await _edit(query, "Envoie /marches pour la liste complète, ou /analyse nvidia 1h "
                           "pour une analyse directe.", reply_markup=market_keyboard())


# ==================================================================
# Job du scan automatique
# ==================================================================
async def scan_job(context: ContextTypes.DEFAULT_TYPE):
    if _scan_lock.locked():
        log.info("Scan précédent encore en cours : cycle ignoré")
        return
    async with _scan_lock:
        try:
            alerts = await scanner.run_scan(context.bot, settings.recipient_ids())
            if alerts:
                log.info("Scan terminé : %s alerte(s) envoyée(s)", alerts)
        except Exception:
            log.exception("Scan en erreur")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """Gestionnaire global : consigne l'erreur et prévient l'utilisateur."""
    log.error("Erreur non gérée : %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Une erreur inattendue est survenue (consignée dans les logs). Réessaie.")
        except Exception:
            pass


# ==================================================================
# Démarrage
# ==================================================================
async def post_init(application: Application):
    resumed = False
    if settings.SCAN_AUTOSTART or history.get_scan_enabled():
        resumed = _start_scan_job(application.job_queue)
    text = ("Bot démarré ! /start pour choisir un marché, /top pour classer les "
            "opportunités, /scan on pour le scan automatique, /stats et /backtest pour "
            "mesurer l'efficacité, /help pour tout voir."
            + ("\nScan automatique : REPRIS." if resumed else ""))
    for chat_id in settings.recipient_ids():
        try:
            await application.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            log.warning("Message de démarrage non envoyé à %s : %s", chat_id, e)


def main():
    if not settings.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN manquant : crée le fichier .env (voir .env.example)")
    if not settings.ADMIN_ID:
        raise SystemExit("ADMIN_ID manquant : récupère ton ID via @userinfobot "
                         "(ou la commande /id du bot) et remplis le .env")

    log.info("Démarrage — scan %s min, points mini %s, marge mini %s, accord mini %s%%, MTF %s",
             settings.SCAN_INTERVAL_MIN, settings.SIGNAL_MIN_POINTS,
             settings.SIGNAL_MIN_MARGIN, settings.SIGNAL_MIN_CONFIDENCE, settings.MTF_FILTER)

    app = (Application.builder().token(settings.BOT_TOKEN).post_init(post_init)
           .concurrent_updates(True).build())
    for name, fn in (("start", cmd_start), ("help", cmd_help), ("analyse", cmd_analyse),
                     ("top", cmd_top), ("marches", cmd_marches), ("scan", cmd_scan),
                     ("watch", cmd_watch), ("stats", cmd_stats), ("backtest", cmd_backtest),
                     ("prix", cmd_prix), ("id", cmd_id)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
