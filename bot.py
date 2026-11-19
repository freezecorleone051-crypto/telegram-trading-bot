"""
Bot Telegram d'analyse technique de trading — usage privé.

Commandes :
  /start               menu de démarrage avec boutons
  /analyse [actif] [tf]  analyse complète + image (ex : /analyse BTC 5m)
  /prix [actif]        prix en direct + variation 24h
  /marches             liste des marchés supportés
  /scan on|off         scan automatique de la watchlist
  /watch ...           gérer la watchlist (add/remove/clear)
  /stats               résultats des signaux passés (TP vs SL)
  /id                  affiche ton ID Telegram
  /help                aide détaillée

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
from core.signal_engine import analyze
from data import feed, markets
from data.feed import FeedError
from outputs import chart, messages
from services import mtf as mtf_service
from services import scanner
from services.mtf import mtf_verdict
from storage import history
from utils.logger import setup_logging
from utils.helpers import time_str

log = setup_logging()

ARROW = messages.ARROW


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
                    "Ce bot est privé. Il n'accepte que son propriétaire.")
            return
        return await func(update, context)
    return wrapper


# ==================================================================
# Analyse complète : cours -> signal -> MTF -> image -> messages
# ==================================================================
async def run_full_analysis(context: ContextTypes.DEFAULT_TYPE, market: str,
                            asset: str, timeframe: str):
    """Chaîne complète. Renvoie (png_bytes, texte, source_label) ou lève FeedError."""
    df, source_label = await asyncio.to_thread(feed.fetch_candles,
                                               market, asset, timeframe, 250)
    signal = await asyncio.to_thread(analyze, df)

    # contrôle multi-timeframe (courtes erreurs tolérées)
    mtf_text = None
    try:
        mtf_results = await asyncio.to_thread(mtf_service.analyze_mtf,
                                              market, asset, timeframe)
        mtf_text = mtf_verdict(mtf_results, signal["direction"])
    except Exception as e:
        log.warning("MTF %s %s : %s", asset, timeframe, e)

    png = await asyncio.to_thread(chart.make_chart, df, signal, asset, timeframe,
                                  source_label)

    # historique : uniquement les signaux confirmés
    if signal["direction"] != "ATTENDRE":
        added = await asyncio.to_thread(history.add_signal, signal, market,
                                        asset, timeframe, source_label)
        if added:
            log.info("Signal enregistré : %s %s %s (%s%%)", asset, timeframe,
                     signal["direction"], signal["confidence"])

    text = messages.build_signal_message(market, asset, timeframe, signal,
                                         source_label, mtf_text)
    return png, text, messages.signal_caption(asset, timeframe, signal)


def _extract_chat_id(obj) -> int | None:
    """Retrouve le chat_id quel que soit l'objet reçu.

    - Update (commande /analyse)     -> update.effective_chat.id
    - CallbackQuery (bouton cliqué)  -> callback.message.chat_id
    - un chat direct                 -> .chat_id
    """
    chat = getattr(obj, "effective_chat", None)
    if chat is not None:
        return chat.id
    msg = getattr(obj, "message", None)
    if msg is not None:
        return msg.chat_id
    return getattr(obj, "chat_id", None)


async def send_analysis(update_or_chat, context: ContextTypes.DEFAULT_TYPE,
                        market: str, asset: str, timeframe: str) -> None:
    chat_id = _extract_chat_id(update_or_chat)
    if chat_id is None:
        log.warning("chat_id introuvable pour l'analyse %s %s : j'envoie a ADMIN_ID",
                    asset, timeframe)
        chat_id = settings.ADMIN_ID

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Analyse de {asset} en {timeframe}… (cours + 6 modules + contrôle "
             "multi-timeframe + image)")
    try:
        png, text, caption = await run_full_analysis(context, market, asset, timeframe)
    except FeedError as e:
        await status.edit_text(
            f"Impossible d'analyser {asset} ({timeframe}).\n"
            f"Détail : {e}\n\nCauses fréquentes : marché fermé (forex le "
            "week-end), API momentanément indisponible, ou actif mal "
            "orthographié (voir /marches). Réessaie dans quelques instants.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
    try:
        await status.delete()
        await context.bot.send_photo(chat_id=chat_id, photo=png, caption=caption[:1024])
    except Exception:
        await status.edit_text(caption[:4096])
    await context.bot.send_message(chat_id=chat_id, text=text,
                                   parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)


# ==================================================================
# Claviers inline
# ==================================================================
def market_keyboard():
    rows = [[InlineKeyboardButton(markets.MARKET_LABELS[m].split(" (")[0],
                                  callback_data=f"cat:{m}")]
            for m in ("crypto", "forex", "otc")]
    rows.append([InlineKeyboardButton("Marchés supportés", callback_data="help:assets")])
    return InlineKeyboardMarkup(rows)


def asset_keyboard(market: str):
    assets = markets.assets_for(market)
    rows, row = [], []
    for a in assets:
        row.append(InlineKeyboardButton(a, callback_data=f"asset:{market}:{a}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("< Changer de marché", callback_data="back:cat")])
    return InlineKeyboardMarkup(rows)


def timeframe_keyboard(market: str, asset: str):
    rows = [[InlineKeyboardButton(tf, callback_data=f"tf:{market}:{asset}:{tf}")]
            for tf in markets.TIMEFRAMES]
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
        "- Tu choisis un marché et un timeframe -> j'analyse le graphique avec "
        "6 modules (EMA 9/21, RSI, MACD, Bollinger, Stochastique, chandeliers "
        "japonais) et je t'envoie un signal ACHAT / VENTE avec entrée, Take "
        "Profit et Stop Loss.\n"
        "- Je contrôle aussi les timeframes supérieurs (multi-timeframe) pour "
        "dire si le signal est renforcé ou fragile.\n"
        "- À chaque analyse je t'envoie une <b>image du graphique</b> avec les "
        "indicateurs et les niveaux tracés.\n"
        "- Je suis les résultats de mes signaux : /stats donne le taux de "
        "réussite réel (TP touché vs SL touché).\n"
        "- Le scan automatique (/scan on) surveille ta watchlist et t'alerte.\n\n"
        "<b>Commandes :</b> /analyse — /prix — /marches — /scan — /watch — "
        "/stats — /help — /id\n\n"
        "Choisis une famille de marché :")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=market_keyboard())


@private_guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Aide</b>\n"
        "/start — menu avec boutons\n"
        "/analyse [actif] [timeframe] — analyse immédiate complète\n"
        "   ex : /analyse BTC 5m | /analyse EUR/USD 15m | /analyse XAU 1h\n"
        "/prix [actif] — prix en direct + variation 24h\n"
        "/marches — liste des actifs\n"
        "/scan on / /scan off — scan automatique de la watchlist\n"
        "/watch add BTC 5m — ajouter à la watchlist\n"
        "/watch remove BTC 5m — retirer ; /watch clear — tout vider\n"
        "/stats — taux de réussite des signaux passés\n"
        "/id — ton ID Telegram\n\n"
        "<b>Comment je décide (système de points, max 12 par côté) :</b>\n"
        "- EMA 9/21 + croisement : jusqu'à 4 points\n"
        "- RSI 14 (zones 30/45/55/70) : jusqu'à 2 points\n"
        "- MACD + histogramme : jusqu'à 2 points\n"
        "- Bandes de Bollinger (extrêmes) : 1 point\n"
        "- Stochastique (croisements en zone 25/75) : 1 point\n"
        "- Chandeliers japonais (pin bar, englobante, marubozu, 3 soldats...) : "
        "jusqu'à 2 points\n\n"
        "Un signal n'est confirmé que si la confiance est au-dessus de "
        f"{settings.SIGNAL_MIN_CONFIDENCE}%, l'écart de points >= "
        f"{settings.SIGNAL_MIN_MARGIN} et le côté dominant >= "
        f"{settings.SIGNAL_MIN_POINTS} points. Sinon je réponds ATTENDRE. "
        "TP/SL calibrés sur l'ATR (volatilité réelle).")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@private_guard
async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Quel marché veux-tu analyser ? Choisis la famille :",
            reply_markup=market_keyboard())
        return

    tf = "5m"
    tokens = list(args)
    if tokens and tokens[-1].lower() in markets.TIMEFRAMES:
        tf = tokens.pop().lower()

    match = markets.find_asset(" ".join(tokens))
    if not match:
        await update.message.reply_text(
            "Actif non reconnu. Voici ce que je connais :\n/marches\n\n"
            "Exemples : /analyse BTC 5m — /analyse EUR/USD 15m — /analyse XAU 1h "
            "— /analyse BTC OTC 5m")
        return
    market, asset = match
    await send_analysis(update, context, market, asset, tf)


@private_guard
async def cmd_marches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Marchés supportés</b>\n\n"
        "<b>Crypto</b> (cours Binance) : " + ", ".join(markets.CRYPTO_ASSETS) + "\n\n"
        "<b>Forex & Or</b> (cours Yahoo) : " + ", ".join(markets.FOREX_ASSETS) + "\n\n"
        "<b>OTC / Binaires</b> (cours du même actif ailleurs -> même graphique) : "
        + ", ".join(markets.OTC_ASSETS) + "\n\n"
        "Timeframes : " + ", ".join(markets.TIMEFRAMES) + "\n\n"
        "Exemples : /analyse BTC 5m — /analyse XAU/USD 15m — /analyse EUR/USD OTC 1m")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@private_guard
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    jobs = context.job_queue.get_jobs_by_name("scan") if context.job_queue else []
    watch = history.get_watchlist()

    if arg == "on":
        if jobs:
            await update.message.reply_text(
                f"Le scan automatique est DÉJÀ actif (toutes les "
                f"{settings.SCAN_INTERVAL_MIN} min). Watchlist : "
                + ", ".join(f"{a} ({tf})" for _m, a, tf in watch))
            return
        context.job_queue.run_repeating(scan_job,
                                        interval=settings.SCAN_INTERVAL_MIN * 60,
                                        first=10, name="scan", chat_id=settings.ADMIN_ID)
        await update.message.reply_text(
            f"Scan automatique ACTIVÉ : je surveille "
            + ", ".join(f"{a} ({tf})" for _m, a, tf in watch)
            + f" toutes les {settings.SCAN_INTERVAL_MIN} min. Je t'enverrai le "
              "message + l'image dès qu'un signal confirmé sort, et je suis "
              "aussi les résultats (voir /stats).")
    elif arg == "off":
        for j in jobs:
            j.schedule_removal()
        await update.message.reply_text("Scan automatique DÉSACTIVÉ.")
    else:
        state = "actif" if jobs else "arrêté"
        await update.message.reply_text(
            f"Scan automatique : actuellement {state}.\n"
            "/scan on pour démarrer — /scan off pour arrêter.\n"
            "Watchlist : " + ", ".join(f"{a} ({m} {tf})" for m, a, tf in watch))


@private_guard
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0].lower() not in ("add", "remove", "clear", "list"):
        await update.message.reply_text(messages.build_watchlist_message(
            history.get_watchlist()))
        return

    action = args[0].lower()
    if action == "clear":
        await asyncio.to_thread(history.watchlist_clear)
        await update.message.reply_text("Watchlist vidée.")
        return
    if action == "list" or len(args) < 2:
        await update.message.reply_text(messages.build_watchlist_message(
            history.get_watchlist()))
        return

    tf = "5m"
    tokens = list(args[1:])
    if tokens and tokens[-1].lower() in markets.TIMEFRAMES:
        tf = tokens.pop().lower()
    match = markets.find_asset(" ".join(tokens))
    if not match:
        await update.message.reply_text(
            "Actif non reconnu (voir /marches). Exemple : /watch add ETH 15m")
        return
    market, asset = match
    if action == "add":
        ok = await asyncio.to_thread(history.watchlist_add, market, asset, tf)
        if ok:
            await update.message.reply_text(
                f"{asset} ({tf}) ajouté à la watchlist. Si le scan est actif "
                "(/scan on), il sera surveillé.")
        else:
            await update.message.reply_text(f"{asset} ({tf}) est déjà dans la "
                                            "watchlist.")
    else:  # remove
        ok = await asyncio.to_thread(history.watchlist_remove, market, asset, tf)
        await update.message.reply_text(f"{asset} ({tf}) retiré." if ok
                                        else f"{asset} ({tf}) n'était pas dans "
                                             "la watchlist.")


@private_guard
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text(
        "Mise à jour des signaux ouverts (vérification TP/SL sur les cours)…")
    try:
        closed = await asyncio.to_thread(history.update_open_signals, feed.fetch_candles)
    except Exception as e:
        log.warning("update_open_signals : %s", e)
        closed = 0
    st = await asyncio.to_thread(history.stats)
    head = (f"Mise à jour : {closed} signal(aux) clôturé(s) depuis la dernière "
            "vérification.\n\n") if closed else ""
    await status.edit_text(head + messages.build_stats_message(st),
                           parse_mode=ParseMode.HTML)


@private_guard
async def cmd_prix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else "BTC"
    match = markets.find_asset(query)
    if not match:
        await update.message.reply_text("Actif non reconnu (voir /marches).")
        return
    market, asset = match
    try:
        df, source_label = await asyncio.to_thread(feed.fetch_candles,
                                                   market, asset, "1h", 30)
    except FeedError as e:
        await update.message.reply_text(
            f"Prix indisponible pour {asset} : {e}\n(marché fermé le week-end "
            "pour le forex ?)")
        return
    price = float(df["close"].iloc[-1])
    ref = float(df["close"].iloc[-25]) if len(df) > 25 else float(df["close"].iloc[0])
    change = (price - ref) / ref * 100 if ref else None
    await update.message.reply_text(
        messages.build_price_message(asset, price, change, source_label),
        parse_mode=ParseMode.HTML)


@private_guard
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ton ID Telegram : <b>{update.effective_user.id}</b>\n"
        "Mets-le dans le fichier .env (ADMIN_ID=...) pour l'accès privé.",
        parse_mode=ParseMode.HTML)


# ==================================================================
# Boutons
# ==================================================================
@private_guard
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    if data == "back:cat" or data.startswith("cat:"):
        market = data.split(":", 1)[1] if ":" in data else ""
        if market in markets.MARKET_LABELS:
            await query.edit_message_text(
                f"Marché : {markets.MARKET_LABELS[market]}\nChoisis l'actif :",
                reply_markup=asset_keyboard(market))
        else:
            await query.edit_message_text("Quelle famille de marché ?",
                                          reply_markup=market_keyboard())
    elif data.startswith("asset:"):
        _, market, asset = data.split(":", 2)
        await query.edit_message_text(
            f"{asset} — choisis le timeframe :",
            reply_markup=timeframe_keyboard(market, asset))
    elif data.startswith("tf:"):
        _, market, asset, tf = data.split(":", 3)
        # NOTE : on passe l'Update complet (il porte la conversation d'origine),
        # pas la CallbackQuery — sinon le chat_id est introuvable ("Chat not found").
        await send_analysis(update, context, market, asset, tf)
    elif data == "help:assets":
        await query.edit_message_text(
            "Crypto : " + ", ".join(markets.CRYPTO_ASSETS) + "\n"
            "Forex : " + ", ".join(markets.FOREX_ASSETS) + "\n"
            "OTC : " + ", ".join(markets.OTC_ASSETS) + "\n\n"
            "Envoie /analyse BTC 5m pour une analyse directe.",
            reply_markup=market_keyboard())


# ==================================================================
# Job du scan automatique
# ==================================================================
async def scan_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        alerts = await scanner.run_scan(context.bot, settings.ADMIN_ID)
        if alerts:
            log.info("Scan terminé : %s alerte(s) envoyée(s)", alerts)
    except Exception as e:
        log.error("Scan en erreur : %s", e)


# ==================================================================
# Erreurs non gérées : log propre au lieu d'une traceback géante
# ==================================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Erreur non gérée : %s", context.error)


# ==================================================================
# Démarrage
# ==================================================================
async def post_init(application: Application):
    try:
        await application.bot.send_message(
            chat_id=settings.ADMIN_ID,
            text="Bot démarré ! /start pour choisir un marché, /scan on pour "
                 "le scan automatique, /stats pour les résultats des signaux, "
                 "/help pour tout voir.")
    except Exception as e:
        log.warning("Message de démarrage non envoyé (%s) — normal si tu n'as "
                    "pas encore ouvert la discussion avec le bot (appuie sur "
                    "START dans Telegram, puis /start).", e)


def main():
    if not settings.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN manquant : crée le fichier .env (voir .env.example)")
    if not settings.ADMIN_ID:
        raise SystemExit("ADMIN_ID manquant : récupère ton ID via @userinfobot "
                         "et remplis le .env")

    log.info("Démarrage — scan %s min, confiance mini %s%%, points mini %s, "
             "marge mini %s", settings.SCAN_INTERVAL_MIN,
             settings.SIGNAL_MIN_CONFIDENCE, settings.SIGNAL_MIN_POINTS,
             settings.SIGNAL_MIN_MARGIN)

    app = Application.builder().token(settings.BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("analyse", cmd_analyse))
    app.add_handler(CommandHandler("marches", cmd_marches))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("prix", cmd_prix))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()