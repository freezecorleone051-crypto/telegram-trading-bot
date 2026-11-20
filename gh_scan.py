"""Run one trading-bot scan from GitHub Actions."""
import asyncio
import logging
import sys

from telegram import Bot

from config import settings
from services import scanner


async def run_scan() -> int:
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN manquant : configure le secret GitHub BOT_TOKEN.")
    if settings.ADMIN_ID <= 0:
        raise RuntimeError("ADMIN_ID manquant ou invalide : configure le secret GitHub ADMIN_ID.")

    async with Bot(token=settings.BOT_TOKEN) as bot:
        alerts = await scanner.run_scan(bot, settings.ADMIN_ID)

    logging.info("Cycle de scan terminé : %s alerte(s) envoyée(s)", alerts)
    return alerts


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_scan())
    except Exception:
        logging.exception("Le cycle de scan a échoué.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
