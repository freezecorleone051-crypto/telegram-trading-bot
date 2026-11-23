"""Construction des messages Telegram (signal, stats, prix, watchlist, /top...)."""
import html

from config import settings
from core import indicators, patterns, risk
from data import markets
from utils.helpers import fmt_age, time_str

ARROW = {"ACHAT": "ACHAT ▲", "VENTE": "VENTE ▼", "ATTENDRE": "ATTENDRE ●"}
DIR_WORD = {"ACHAT": "haussier", "VENTE": "baissier", "ATTENDRE": "neutre"}
TG_LIMIT = 4000          # marge sous la limite Telegram de 4096 caractères

OTC_WARNING = ("⚠ OTC : ce prix est fabriqué par ton courtier (surtout le week-end) "
               "et n'est PAS celui du vrai marché. L'analyse ci-dessus porte sur le "
               "vrai actif ; elle peut ne pas refléter le graphique OTC que tu vois.")


def market_label(market: str) -> str:
    return markets.MARKET_LABELS.get(market, market)


def asset_title(market: str, asset: str) -> str:
    a = markets.get_asset(market, asset)
    if a and a.label and a.label != asset:
        return f"{asset} · {a.label}"
    return asset


def signal_caption(asset: str, timeframe: str, signal: dict) -> str:
    base = (f"{asset} — {timeframe} — {ARROW[signal['direction']]} "
            f"(score {signal.get('score', 0)}/{signal.get('max_points', 14)}, "
            f"accord {signal['confidence']}%)")
    if signal["direction"] == "ATTENDRE":
        return base
    return base + f"\nEntrée {signal['entry']} | TP {signal['tp']} | SL {signal['sl']}"


def _fit(head: list, reasons: list, tail: list) -> str:
    """Assemble le message et retire des raisons si on dépasse la limite Telegram."""
    reasons = list(reasons)
    while True:
        text = "\n".join(head + reasons + tail)
        if len(text) <= TG_LIMIT or not reasons:
            return text[:4096]
        reasons.pop(len(reasons) // 2)      # retire une raison au milieu


def build_signal_message(market: str, asset: str, timeframe: str, signal: dict,
                         source_label: str, mtf_text: str | None = None,
                         stale: bool = False, age_s: float = 0.0) -> str:
    ind = signal["indicators"]
    direction = signal["direction"]
    head = [f"<b>SIGNAL — {html.escape(asset_title(market, asset))} ({timeframe})</b>",
            f"Marché : {html.escape(market_label(market))}",
            f"Source des cours : {html.escape(source_label)}"]
    if stale:
        head.append(f"⚠ <b>Marché fermé ou données anciennes</b> (dernière bougie fermée "
                    f"il y a {fmt_age(age_s)}). Niveaux basés sur la dernière clôture — "
                    "rien n'est exécutable maintenant.")
    head.append("")

    regime = signal.get("regime", "")
    regime_txt = ""
    if regime:
        regime_txt = (f" — régime : {regime} (ADX {ind['adx']:.0f})"
                      if ind.get("adx") == ind.get("adx") else f" — régime : {regime}")

    if direction == "ATTENDRE":
        if signal.get("blocked_by"):
            head.append(f"<b>Direction : ATTENDRE ●</b> — un signal {signal.get('raw_direction')} "
                        f"existait mais il est ANNULÉ : {html.escape(signal['blocked_by'].rstrip('. '))}.")
        else:
            lean = ("acheteur" if ind["buy_points"] > ind["sell_points"]
                    else "vendeur" if ind["sell_points"] > ind["buy_points"] else "neutre")
            head.append(f"<b>Direction : ATTENDRE ●</b> — biais {lean}, signal non confirmé "
                        f"({ind['buy_points']} pts acheteurs vs {ind['sell_points']} vendeurs"
                        f"{regime_txt}). On reste à l'écart, prudence.")
        # pas de TP/SL sur ATTENDRE : des niveaux à côté d'un « n'entre pas » invitent à
        # trader contre l'avis du bot (la v1 les affichait aussi sur le graphique)
        head.append(f"Dernière clôture : {signal['entry']}")
    else:
        rr_txt = (f"R:R 1:{ind['rr']:g}" if ind["rr"] >= 1
                  else f"ATTENTION R:R faible 1:{ind['rr']:g}")
        head.append(f"<b>Direction : {ARROW[direction]}</b> — score {signal.get('score', 0)}/"
                    f"{signal.get('max_points', 14)}, accord des indicateurs {signal['confidence']}%, "
                    f"{rr_txt}{regime_txt}")
    if direction != "ATTENDRE":
        head += [
            f"Entrée : {signal['entry']}  (clôture de la dernière bougie)",
            f"Take Profit : {signal['tp']}  (+{ind['tp_pct']:.2f}%)  — entrée ± {settings.TP_ATR:g} x ATR",
            f"Stop Loss : {signal['sl']}  (-{ind['sl_pct']:.2f}%)  — entrée ∓ {settings.SL_ATR:g} x ATR",
        ]
        hint = risk.position_hint(signal["entry"], signal["sl"], market)
        if hint:
            head.append(f"Position : {html.escape(hint)}")
    head += ["", "<b>Comment le bot a décidé :</b>"]

    reasons = [f"{i}. {html.escape(r)}" for i, r in enumerate(signal["reasons"], 1)]

    macd_state = ("MACD au-dessus du signal" if ind["macd"] > ind["macd_signal"]
                  else "MACD sous le signal")
    vol = ("" if ind.get("vol_ratio") != ind.get("vol_ratio")
           else f", volume x{ind['vol_ratio']:.1f}")
    tail = [
        "",
        html.escape(patterns.summarize(signal.get("patterns", []))),
        f"\nRésumé : RSI(14) {ind['rsi']:.1f}, {macd_state}, Bollinger {ind['bb_pos']:.0%} des "
        f"bandes, Stochastique %K {ind['stoch_k']:.0f}/%D {ind['stoch_d']:.0f}, "
        f"ATR(14) {indicators.smart_round(ind['atr'])} ({ind['atr_pct']:.2f}% du prix){vol}.",
    ]
    if mtf_text:
        tail.append(html.escape(mtf_text))
    if market == "otc":
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(timeframe, 5)
        tail.append(f"Option binaire : expiration conseillée ~2 bougies ({minutes * 2} min).")
        tail.append(html.escape(OTC_WARNING))
    tail += [f"\nHeure : {time_str()} ({settings.TZ_NAME})",
             f"\n<i>« Accord » mesure la concordance des indicateurs, ce n'est PAS une "
             f"probabilité de gagner. {html.escape(settings.DISCLAIMER)}</i>"]
    return _fit(head, reasons, tail)


def build_stats_message(st: dict) -> str:
    lines = ["<b>Résultats réels des signaux suivis</b>", ""]
    if st["closed"] == 0:
        lines.append("Aucun signal clôturé pour l'instant. Les signaux confirmés sont suivis "
                     "automatiquement : le contrôle tourne à chaque scan et à chaque /stats.")
        if st["open"]:
            lines.append(f"\nEn cours : {st['open']} signal(aux) ouvert(s).")
    else:
        lines.append(f"Signaux clôturés : {st['closed']} — {st['win']} TP, {st['loss']} SL, "
                     f"{st['expired']} expirés (sortie à la clôture après le délai maximum)")
        if st["win_rate"] is not None:
            lo, hi = st["win_rate_ci"]
            be = (f" — seuil de rentabilité ≈ {st['breakeven_win_rate']:.0%}"
                  if st["breakeven_win_rate"] else "")
            lines.append(f"Taux de réussite TP vs SL : {st['win_rate']:.0%} "
                         f"(intervalle 95% : {lo:.0%}-{hi:.0%}){be}")
        pf = ("∞" if st["profit_factor"] == float("inf")
              else f"{st['profit_factor']:.2f}" if st["profit_factor"] is not None else "n/a")
        lines.append(f"Espérance : <b>{st['avg_r']:+.2f} R</b> par signal — total "
                     f"{st['total_r']:+.1f} R — facteur de profit {pf}")
        lines.append(f"Ouverts : {st['open']} — total enregistré : {st['total']}")
        if st["unknown"]:
            lines.append(f"Non évaluables (historique de cours trop court) : {st['unknown']}")
        lines.append("")
        lines.append("<b>Par actif :</b>")
        for asset, a in sorted(st["per_asset"].items(),
                               key=lambda kv: -(kv[1]["win"] + kv[1]["loss"] + kv[1]["expired"])):
            lines.append(f"- {html.escape(asset)} : {a['win']} TP / {a['loss']} SL / "
                         f"{a['expired']} exp. → {a['r']:+.1f} R")
        if st["decided"] < 30:
            lines.append("")
            lines.append(f"⚠ Seulement {st['decided']} signaux tranchés : trop peu pour conclure "
                         "(il en faut 30 minimum, 100 pour être sérieux). Un bon score sur "
                         "10 signaux peut n'être que de la chance.")
    lines.append("")
    lines.append("R = le risque pris sur un trade (SL touché = -1 R, TP touché = +RR). "
                 "Hypothèse conservatrice : TP et SL dans la même bougie = perte.")
    lines.append(f"\n<i>{html.escape(settings.DISCLAIMER)}</i>")
    return "\n".join(lines)


def build_price_message(asset: str, price: float, change_pct: float | None,
                        source_label: str, span: str = "sur 24h",
                        decimals: int | None = None, query: str | None = None,
                        live: bool = False) -> str:
    ch = (f"{change_pct:+.2f}% {span}" if change_pct is not None
          else f"variation {span} indisponible")
    shown = f"{price:.{decimals}f}" if decimals is not None else f"{indicators.smart_round(price):g}"
    q = query or asset.replace("/USDT", "")
    freshness = "tick en direct" if live else "dernière bougie disponible"
    return (f"<b>{html.escape(asset)}</b> : {shown}\n"
            f"{ch}\nCours : {freshness}\nSource : {html.escape(source_label)}\n\n"
            f"Analyse complète : /analyse {html.escape(q)}")


def build_watchlist_message(items: list) -> str:
    if not items:
        return ("Watchlist vide. Ajoute des marchés avec :\n"
                "/watch add NVDA 1h\n/watch add BTC 15m\n/watch add EUR/USD 15m")
    lines = ["<b>Watchlist surveillée par le scan automatique :</b>"]
    for m, a, t in items:
        lines.append(f"- {html.escape(asset_title(m, a))} ({t}, {html.escape(m)})")
    lines.append("\nAjouter : /watch add ACTIF [timeframe] — ex : /watch add nvidia 1h")
    lines.append("Retirer : /watch remove ACTIF [timeframe] — tout vider : /watch clear")
    return "\n".join(lines)


def build_markets_message() -> str:
    hfm_catalogue = markets.hfm_assets()
    if hfm_catalogue:
        lines = ["<b>Catalogue de ton compte HFM MT5</b>",
                 f"{len(hfm_catalogue)} instruments négociables détectés.",
                 "Les symboles ci-dessous sont les noms exacts lus dans ton terminal.", ""]
        for market in markets.MARKET_ORDER:
            items = markets.hfm_assets(market)
            if not items:
                continue
            shown = [item.name for item in items[:8]]
            remaining = len(items) - len(shown)
            heading = f"{markets.MARKET_INFO[market]['button']} — {len(items)}"
            lines.append(f"<b>{html.escape(heading)}</b>")
            lines.append(html.escape(", ".join(shown)))
            if remaining:
                lines.append(f"… et {remaining} autres. Utilise /analyse SYMBOLE 15m.")
            lines.append("")
        lines.append("/hfm refresh — actualiser le catalogue ; /scan on — surveiller "
                     "tous ces instruments ; /analyse SYMBOLE 15m — analyser un instrument.")
        return "\n".join(lines)

    # Mode hors connexion HFM : garde l'ancien catalogue manuel.
    lines = ["<b>Marchés historiques disponibles (hors catalogue HFM)</b>", ""]
    for market in markets.MARKET_ORDER:
        static_items = markets.CATALOG.get(market, [])
        if not static_items:
            continue
        names = [item.name if (not item.label or item.label == item.name
                               or market in ("crypto", "forex", "otc"))
                 else f"{item.name} ({item.label})" for item in static_items]
        lines.append(f"<b>{html.escape(market_label(market))}</b>\n"
                     + html.escape(", ".join(names)) + "\n")
    lines.append("Pour connecter ton compte HFM : ouvre MT5 sur Windows puis tape /hfm.")
    return "\n".join(lines)


MTF_FR = {"aligned": "TF supérieur aligné", "partial": "TF supérieur partiellement contre",
          "neutral": "TF supérieur neutre", "against": "⚠ TF supérieur CONTRE"}


def build_top_message(market: str, timeframe: str, ranked: list, no_signal: list,
                      errors: list, stale_count: int) -> str:
    lines = [f"<b>Opportunités — {html.escape(market_label(market))} — {timeframe}</b>", ""]
    if ranked:
        shown = ranked[:25]
        for r in shown:
            sg = r["signal"]
            ind = sg["indicators"]
            mtf_txt = MTF_FR.get(r.get("mtf_status") or "", "")
            lines.append(
                f"{ARROW[sg['direction']]}  <b>{html.escape(asset_title(market, r['asset']))}</b> — "
                f"score {sg['score']}/{sg['max_points']}, régime {sg['regime']}, RSI {ind['rsi']:.0f}, "
                f"ATR {ind['atr_pct']:.2f}%" + (f" — {mtf_txt}" if mtf_txt else ""))
        lines.append("\nDétail + graphique : /analyse ACTIF " + timeframe)
    else:
        lines.append("Aucun signal confirmé pour l'instant. C'est une réponse normale : "
                     "la plupart du temps, il n'y a rien à faire.")
    if no_signal:
        lines.append("\nSans signal : " + html.escape(", ".join(no_signal)))
    if stale_count:
        lines.append(f"\n⚠ {stale_count} actif(s) ignoré(s) : marché fermé ou données anciennes.")
    if errors:
        lines.append("Indisponibles : " + html.escape(", ".join(errors)))
    lines.append(f"\n<i>{html.escape(settings.DISCLAIMER)}</i>")
    text = "\n".join(lines)
    if len(text) > 4096:          # jamais couper au milieu d'une balise HTML
        text = text[:4000].rsplit("\n", 1)[0] + f"\n… (liste tronquée)\n\n<i>{html.escape(settings.DISCLAIMER)}</i>"
    return text


def build_result_message(sig: dict) -> str:
    """Notification quand un signal suivi est clôturé (TP / SL / expiré)."""
    st = sig["status"]
    head = {"win": "✅ TP touché", "loss": "❌ SL touché",
            "expired": "⏱ Signal expiré (sortie à la clôture)"}.get(st, st)
    return (f"{head} — {html.escape(sig['asset'])} {sig['tf']} {sig['direction']}\n"
            f"Entrée {sig['entry']} → TP {sig['tp']} / SL {sig['sl']}\n"
            f"Résultat : {sig.get('r', 0):+.2f} R")
