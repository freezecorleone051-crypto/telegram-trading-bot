"""Un seul cycle de scan, puis arrêt — pour GitHub Actions (ou n'importe quel cron).

Pas de bot permanent : à chaque exécution le script
  1. vérifie la configuration (BOT_TOKEN, ADMIN_ID) et dit clairement ce qui manque ;
  2. met à jour les signaux ouverts (TP / SL touchés ?) et notifie les résultats ;
  3. analyse la watchlist et envoie les alertes (image + message) à ADMIN_ID ;
  4. s'arrête avec un code de sortie exploitable.

Usage :
    python scan_once.py              # un cycle de scan
    python scan_once.py --test       # envoie seulement un message de test (vérifie token + ID)

Codes de sortie :
    0  cycle terminé (même s'il n'y avait aucune alerte : c'est le cas normal)
    1  échec : erreur inattendue, ou TOUTES les analyses ont échoué (réseau, source de cours)
    2  configuration manquante ou invalide
    3  délai dépassé (SCAN_TIMEOUT_S)

IMPORTANT — mémoire entre deux exécutions : l'historique des signaux
(data_store/signals.json) sert à ne pas renvoyer deux fois la même alerte et à suivre
les résultats. Sur GitHub Actions le disque est vierge à chaque exécution : le workflow
fourni (.github/workflows/scan.yml) le sauvegarde et le restaure avec actions/cache.
Sans cela, le même signal serait renvoyé à chaque cycle.

La watchlist vient de la variable d'environnement WATCHLIST (format
marché:actif:timeframe, séparés par des virgules) : les commandes /watch du bot ne sont
pas disponibles dans ce mode.
"""
import argparse
import asyncio
import logging
import os
import sys
import time

from config import settings
from utils.logger import setup_logging

log = logging.getLogger("tradingbot.scan_once")

EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_TIMEOUT = 0, 1, 2, 3
DEFAULT_TIMEOUT_S = 240


class ConfigError(RuntimeError):
    """Configuration absente ou invalide (message destiné à l'utilisateur)."""


def check_config() -> None:
    """Lève ConfigError avec la liste de TOUS les problèmes, pas seulement le premier."""
    problems = []
    if not settings.BOT_TOKEN:
        problems.append("BOT_TOKEN manquant : crée le secret GitHub « BOT_TOKEN » "
                        "(token donné par @BotFather).")
    elif ":" not in settings.BOT_TOKEN:
        problems.append("BOT_TOKEN invalide : il doit ressembler à 123456789:AAxxxx "
                        "(copie-le en entier depuis @BotFather).")
    if settings.ADMIN_ID <= 0:
        problems.append("ADMIN_ID manquant ou invalide : crée le secret GitHub « ADMIN_ID » "
                        "avec ton ID Telegram numérique (écris à @userinfobot pour le lire).")
    if problems:
        raise ConfigError("\n".join(problems))


def _make_bot():
    """Bot Telegram avec des délais adaptés à l'envoi d'images (le défaut de 5 s
    en écriture est trop court sur un serveur partagé). Import tardif : le module reste
    importable (et testable) sans la librairie telegram."""
    from telegram import Bot
    from telegram.request import HTTPXRequest

    request = HTTPXRequest(connect_timeout=15, read_timeout=30,
                           write_timeout=30, pool_timeout=15)
    return Bot(token=settings.BOT_TOKEN, request=request)


def _timeout_s() -> int:
    try:
        return max(30, int(os.getenv("SCAN_TIMEOUT_S", DEFAULT_TIMEOUT_S)))
    except ValueError:
        return DEFAULT_TIMEOUT_S


async def send_test_message() -> None:
    """Message de test : prouve que le token et l'ID sont bons."""
    text = ("✅ Scan GitHub Actions opérationnel : ton bot peut t'écrire.\n"
            "Tu recevras ici les alertes ACHAT / VENTE confirmées.")
    async with _make_bot() as bot:
        for cid in settings.recipient_ids():
            await bot.send_message(chat_id=cid, text=text)


async def run_scan() -> dict:
    """Un cycle de scan. Renvoie le bilan (alertes, erreurs...)."""
    from services import scanner          # import tardif : charge pandas/matplotlib seulement ici
    from storage import history

    watch = history.get_watchlist()
    if not watch:
        log.warning("Watchlist vide : rien à scanner (définis la variable WATCHLIST).")
    started = time.monotonic()
    async with _make_bot() as bot:
        alerts = await scanner.run_scan(bot, settings.recipient_ids())
    bilan = dict(scanner.LAST_RUN)
    bilan["alerts"] = alerts
    bilan["seconds"] = round(time.monotonic() - started, 1)
    return bilan


def _write_summary(bilan: dict) -> None:
    """Petit résumé affiché dans l'onglet « Summary » d'une exécution GitHub Actions."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                "### Cycle de scan\n"
                f"- Actifs surveillés : **{bilan.get('watch', 0)}**\n"
                f"- Alertes envoyées : **{bilan.get('alerts', 0)}**\n"
                f"- Sans signal : {bilan.get('no_signal', 0)} · marché fermé / données anciennes : "
                f"{bilan.get('stale', 0)} · déjà envoyés ou en attente : "
                f"{bilan.get('duplicate', 0) + bilan.get('cooldown', 0)}\n"
                f"- Résultats de signaux clôturés : {bilan.get('closed', 0)}\n"
                f"- Erreurs d'analyse : {bilan.get('errors', 0)} · "
                f"erreurs d'envoi : {bilan.get('send_errors', 0)}\n"
                f"- Durée : {bilan.get('seconds', 0)} s\n")
    except OSError as e:
        log.debug("Résumé non écrit : %s", e)


def _verdict(bilan: dict) -> int:
    """Un cycle où TOUTES les analyses échouent n'est pas « réussi » : on le signale
    en échec pour que GitHub avertisse (sinon l'exécution reste verte et muette)."""
    watch, errors = bilan.get("watch", 0), bilan.get("errors", 0)
    if watch and errors >= watch:
        log.error("Toutes les analyses ont échoué (%d/%d) : source de cours inaccessible ?",
                  errors, watch)
        return EXIT_FAIL
    if bilan.get("send_errors", 0) and not bilan.get("alerts", 0):
        log.error("Des alertes n'ont pas pu être envoyées (%d) : vérifie BOT_TOKEN / ADMIN_ID, "
                  "et que tu as bien écrit /start à ton bot au moins une fois.",
                  bilan["send_errors"])
        return EXIT_FAIL
    return EXIT_OK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Un cycle de scan (GitHub Actions / cron).")
    parser.add_argument("--test", action="store_true",
                        help="envoie seulement un message de test à ADMIN_ID")
    args = parser.parse_args(argv)

    setup_logging()
    for w in settings.CONFIG_WARNINGS:
        log.warning("Configuration : %s", w)

    try:
        check_config()
    except ConfigError as e:
        for line in str(e).splitlines():
            log.error(line)
        return EXIT_CONFIG

    try:
        if args.test:
            asyncio.run(asyncio.wait_for(send_test_message(), timeout=60))
            log.info("Message de test envoyé à %s.", settings.ADMIN_ID)
            return EXIT_OK
        bilan = asyncio.run(asyncio.wait_for(run_scan(), timeout=_timeout_s()))
    except (asyncio.TimeoutError, TimeoutError):
        log.error("Délai dépassé (%s s) : cycle interrompu.", _timeout_s())
        # Un thread réseau bloqué empêcherait l'arrêt propre ; l'historique est écrit
        # de façon atomique, on peut donc quitter sans risque.
        logging.shutdown()
        os._exit(EXIT_TIMEOUT)
    except Exception as e:
        if type(e).__name__ == "InvalidToken":
            log.error("BOT_TOKEN refusé par Telegram : vérifie le secret GitHub BOT_TOKEN.")
            return EXIT_CONFIG
        log.exception("Le cycle de scan a échoué.")
        return EXIT_FAIL

    log.info("Cycle terminé : %s alerte(s), %s actif(s), %s sans signal, %s marché fermé, "
             "%s erreur(s), %s s.", bilan["alerts"], bilan["watch"], bilan["no_signal"],
             bilan["stale"], bilan["errors"], bilan["seconds"])
    _write_summary(bilan)
    return _verdict(bilan)


if __name__ == "__main__":
    sys.exit(main())