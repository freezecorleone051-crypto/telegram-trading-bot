"""Construction des messages Telegram (signal, stats, prix, watchlist)."""
import html

from config import settings
from core import indicators, patterns, risk
from utils.helpers import time_str

ARROW = {"ACHAT": "ACHAT ▲", "VENTE": "VENTE ▼", "ATTENDRE": "ATTENDRE ●"}
DIR_WORD = {"ACHAT": "haussier", "VENTE": "baissier", "ATTENDRE": "neutre"}


def signal_caption(asset: str, timeframe: str, signal: dict) -> str:
    return (f"{asset} — {timeframe} — {ARROW[signal['direction']]} "
            f"(confiance {signal['confidence']}%)\n"
            f"Entrée {signal['entry']} | TP {signal['tp']} | SL {signal['sl']}")


def build_signal_message(market: str, asset: str, timeframe: str, signal: dict,
                         source_label: str, mtf_text: str | None = None) -> str:
    ind = signal["indicators"]
    macd_state = ("MACD au-dessus du signal (haussier)" if ind["macd"] > ind["macd_signal"]
                  else "MACD en dessous du signal (baissier)")

    lines = [
        f"<b>SIGNAL — {html.escape(asset)} ({timeframe})</b>",
        f"Marché : {html.escape(settings_market_label(market))}",
        f"Source des cours : {html.escape(source_label)}",
        "",
    ]
    if signal["direction"] == "ATTENDRE":
        lines.append(
            f"<b>Direction : ATTENDRE ●</b> — biais {DIR_WORD['ATTENDRE']} pour "
            f"l'instant, signal non confirmé ({ind['buy_points']} points acheteurs "
            f"vs {ind['sell_points']} vendeurs, minimum {settings.SIGNAL_MIN_POINTS} "
            "requis pour valider). On reste à l'écart, prudence.")
    else:
        rr_txt = (f"ratio risque/rendement 1:{ind['rr']:g}" if ind["rr"] >= 1
                  else f"ATTENTION ratio risque/rendement faible 1:{ind['rr']:g}")
        lines.append(f"<b>Direction : {ARROW[signal['direction']]}</b>  "
                     f"(confiance {signal['confidence']}%, {rr_txt})")

    lines += [
        f"Entrée : {signal['entry']}",
        f"Take Profit : {signal['tp']}  (+{ind['tp_pct']:.2f}%)  "
        f"— entrée + {settings.TP_ATR:g} x ATR",
        f"Stop Loss : {signal['sl']}  (-{ind['sl_pct']:.2f}%)  "
        f"— entrée - {settings.SL_ATR:g} x ATR",
    ]
    hint = risk.position_hint(signal["entry"], signal["sl"])
    if hint:
        lines.append(f"Position : {hint}")

    lines += ["", "<b>Comment le bot a décidé :</b>"]
    for i, reason in enumerate(signal["reasons"], 1):
        lines.append(f"{i}. {html.escape(reason)}")

    lines.append("")
    lines.append(html.escape(patterns.summarize(signal.get("patterns", []))))

    lines += [
        f"\nRésumé : {ind['buy_points']} points acheteurs vs {ind['sell_points']} "
        f"point{'s' if ind['sell_points'] > 1 else ''} vendeur"
        f"{'s' if ind['sell_points'] > 1 else ''}. "
        f"RSI(14) {ind['rsi']:.1f}, {macd_state}, "
        f"Bollinger position {ind['bb_pos']:.0%} des bandes, "
        f"Stochastique %K {ind['stoch_k']:.0f}/%D {ind['stoch_d']:.0f}, "
        f"ATR(14) {indicators.smart_round(ind['atr'])} "
        "(volatilité utilisée pour calibrer TP/SL).",
    ]
    if mtf_text:
        lines.append(html.escape(mtf_text))
    if market == "otc":
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(timeframe, 5)
        lines.append(f"Option binaire : expiration conseillée ~2 bougies "
                     f"({minutes * 2} min).")
    lines += [f"\nHeure : {time_str()} ({settings.TZ_NAME})",
              f"\n<i>{html.escape(settings.DISCLAIMER)}</i>"]
    return "\n".join(lines)


def settings_market_label(market: str) -> str:
    from data import markets
    return markets.MARKET_LABELS.get(market, market)


def build_stats_message(stats: dict) -> str:
    lines = ["<b>Statistiques des signaux (TP touché vs SL touché)</b>", ""]
    done = stats["win"] + stats["loss"]
    if done == 0:
        lines.append("Aucun signal clôturé pour l'instant. Les signaux confirmés "
                     "sont suivis automatiquement : le check tourne à chaque "
                     "scan et à chaque /stats.")
    else:
        rate = stats["win"] / done * 100
        lines.append(f"Signaux clôturés : {done} — "
                     f"{stats['win']} TP touché, {stats['loss']} SL touché "
                     f"-> taux de réussite {rate:.0f}%")
        lines.append(f"Ouverts en cours : {stats['open']} (sur {stats['total']} "
                     "signaux enregistrés)")
        lines.append("")
        lines.append("<b>Par actif :</b>")
        for asset, a in sorted(stats["per_asset"].items(),
                               key=lambda kv: -(kv[1]["win"] + kv[1]["loss"])):
            n = a["win"] + a["loss"]
            lines.append(f"- {html.escape(asset)} : {a['win']} TP / {a['loss']} SL "
                         f"({a['win'] / n * 100:.0f}% de réussite)")
    lines.append("")
    lines.append("Hypothèse conservatrice : si TP et SL sont touchés dans la "
                 "même bougie, le signal compte comme perdu.")
    lines.append(f"\n<i>{html.escape(settings.DISCLAIMER)}</i>")
    return "\n".join(lines)


def build_price_message(asset: str, price: float, change_pct: float | None,
                        source_label: str) -> str:
    ch = (f"{change_pct:+.2f}% sur 24h" if change_pct is not None
          else "variation 24h indisponible")
    return (f"<b>{html.escape(asset)}</b> : {indicators.smart_round(price)}\n"
            f"{ch}\nSource : {html.escape(source_label)}\n\n"
            f"Analyse complète : /analyse {html.escape(asset.replace('/USDT', '').replace(' OTC', ' OTC'))}")


def build_watchlist_message(items: list) -> str:
    if not items:
        return ("Watchlist vide. Ajoute des marchés avec :\n"
                "/watch add BTC/USDT 5m\n/watch add EUR/USD OTC 1m\n"
                "/watch add XAU 15m")
    lines = ["<b>Watchlist surveillée par le scan automatique :</b>"]
    for m, a, t in items:
        lines.append(f"- {html.escape(a)} ({t}, {html.escape(m)})")
    lines.append("\nAjouter : /watch add ACTIF [timeframe] — ex : /watch add ETH 15m")
    lines.append("Retirer : /watch remove ACTIF [timeframe] — tout vider : /watch clear")
    return "\n".join(lines)
