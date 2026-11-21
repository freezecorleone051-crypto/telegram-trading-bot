"""
Moteur de signal v2 : combine les modules d'analyse en un verdict.

NOUVEAUTÉS par rapport à la v1 (voir README, section « Ce qui a changé ») :
  * Le moteur lit le RÉGIME du marché avec l'ADX. En marché en TENDANCE (ADX
    >= 25) il suit la tendance ; en marché sans direction (range) il joue les
    retours à la moyenne. La v1 mélangeait les deux logiques (RSI/Bollinger en
    contre-tendance + EMA/MACD en tendance) : les indicateurs se neutralisaient
    ou se contredisaient en pleine tendance.
  * Il ne travaille que sur des bougies FERMÉES (fournies par data/feed.py).
  * Filtres de qualité : marché trop calme, prix trop étiré, entrée tardive.
  * Tous les calculs sont causaux (aucune fuite du futur) : `analyze_at()` ne
    regarde jamais au-delà de la bougie i — c'est ce qui rend le backtest honnête.

Modules et points maximum (par côté), selon le régime détecté :

  RÉGIME TENDANCE (ADX >= 25) — on suit la tendance :
  - Tendance EMA 9/21/50 + croisement récent : 4
  - MACD(12,26,9)                            : 2
  - ADX / DI (tendance confirmée)            : 1
  - RSI(14) (élan sain / repli)              : 2
  - Bandes de Bollinger (côté de la tendance): 1
  - Stochastique (fin de repli)              : 1
  - Chandeliers (dans le sens de la tendance): 2
  - Volume (si disponible)                   : 1
                                              ----
                                              max 14

  RÉGIME RANGE (ADX < 25) — on joue les retours à la moyenne ; EMA et MACD,
  qui suivent le mouvement, ne sont plus comptés (ils voteraient contre la
  logique de retour à la moyenne) :
  - Écart à l'EMA21 en ATR (z-score)         : 3
  - RSI(14) extrêmes                         : 2
  - Bandes de Bollinger (extrêmes)           : 1
  - Stochastique (croisement aux extrêmes)   : 1
  - Chandeliers                              : 2
  - Volume (si disponible)                   : 1
                                              ----
                                              max 10

  Les seuils SIGNAL_MIN_POINTS / SIGNAL_MIN_MARGIN sont exprimés pour le
  régime tendance (14 pts) et mis à l'échelle en range (10 pts).

Un signal ACHAT/VENTE n'est annoncé que si :
  points du côté dominant >= SIGNAL_MIN_POINTS
  ET écart de points >= SIGNAL_MIN_MARGIN
  ET accord (dominant / total) >= SIGNAL_MIN_CONFIDENCE %
  ET aucun filtre de qualité n'est violé.
Sinon le verdict est ATTENDRE (mieux vaut ne rien dire que d'inventer un signal).

IMPORTANT : « confiance » = accord entre les indicateurs, PAS une probabilité
de gagner. Seul le backtest / le suivi réel (/stats) mesure l'efficacité.
"""
import math

import pandas as pd

from config import settings
from core import indicators, patterns
from core.risk import build_levels

DIRECTIONS = ("ACHAT", "VENTE", "ATTENDRE")
MAX_POINTS = 14           # régime tendance
MAX_POINTS_RANGE = 10     # régime range
MIN_BARS = 60


class InsufficientData(ValueError):
    """Pas assez de bougies pour calculer les indicateurs."""


def _f(series: pd.Series, i: int) -> float:
    v = series.iloc[i]
    return float(v) if pd.notna(v) else float("nan")


def _nan(x: float) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ------------------------------------------------------------------
# Modules de score — chacun renvoie (points_achat, points_vente, raisons)
# ------------------------------------------------------------------
def _score_trend(s, i, close):
    buy = sell = 0
    why = []
    e9, e21, e50 = _f(s["ema9"], i), _f(s["ema21"], i), _f(s["ema50"], i)
    r = indicators.smart_round
    if e9 > e21:
        buy += 1
    elif e9 < e21:
        sell += 1
    if close > e21:
        buy += 1
    elif close < e21:
        sell += 1
    if not _nan(e50):
        if e21 > e50:
            buy += 1
        elif e21 < e50:
            sell += 1
    if buy >= 3:
        why.append(f"Tendance : haussière — EMA9 ({r(e9)}) > EMA21 ({r(e21)}) > EMA50 "
                   f"({r(e50)}) et prix au-dessus ({buy}/3)")
    elif sell >= 3:
        why.append(f"Tendance : baissière — EMA9 ({r(e9)}) < EMA21 ({r(e21)}) < EMA50 "
                   f"({r(e50)}) et prix en dessous ({sell}/3)")
    elif buy == sell:
        why.append("Tendance : moyennes mobiles contradictoires — pas de tendance claire")
    elif buy > sell:
        why.append(f"Tendance : légèrement haussière ({buy}/3 critères) mais pas alignée")
    else:
        why.append(f"Tendance : légèrement baissière ({sell}/3 critères) mais pas alignée")

    # croisement EMA9/EMA21 sur les 3 dernières bougies fermées
    for k in range(max(1, i - 2), i + 1):
        d_prev = _f(s["ema9"], k - 1) - _f(s["ema21"], k - 1)
        d_now = _f(s["ema9"], k) - _f(s["ema21"], k)
        if d_prev <= 0 < d_now and e9 > e21:
            buy += 1
            why.append("Croisement haussier EMA9/EMA21 sur les 3 dernières bougies")
            break
        if d_prev >= 0 > d_now and e9 < e21:
            sell += 1
            why.append("Croisement baissier EMA9/EMA21 sur les 3 dernières bougies")
            break
    return buy, sell, why


def _score_macd(s, i):
    buy = sell = 0
    h, h_prev = _f(s["macd_hist"], i), _f(s["macd_hist"], i - 1)
    if h > 0 and h > h_prev:
        buy = 2
        why = "MACD : au-dessus de sa ligne signal ET histogramme en hausse — élan haussier qui se renforce"
    elif h > 0:
        buy = 1
        why = "MACD : au-dessus de sa ligne signal mais l'élan faiblit"
    elif h < 0 and h < h_prev:
        sell = 2
        why = "MACD : sous sa ligne signal ET histogramme en baisse — élan baissier qui se renforce"
    elif h < 0:
        sell = 1
        why = "MACD : sous sa ligne signal mais l'élan baissier faiblit"
    else:
        why = "MACD : neutre"
    return buy, sell, [why]


def _score_adx(adx_v, pdi, mdi, trending):
    if _nan(adx_v):
        return 0, 0, ["ADX : indisponible (pas assez de bougies) — marché supposé sans tendance"]
    if trending:
        if pdi > mdi:
            return 1, 0, [f"ADX {adx_v:.0f} : TENDANCE forte, côté acheteur (DI+ {pdi:.0f} > DI- {mdi:.0f})"]
        return 0, 1, [f"ADX {adx_v:.0f} : TENDANCE forte, côté vendeur (DI- {mdi:.0f} > DI+ {pdi:.0f})"]
    return 0, 0, [f"ADX {adx_v:.0f} : pas de tendance (< {settings.ADX_TREND:g}) — marché en range, "
                  "le bot privilégie les retours à la moyenne"]


def _score_rsi(s, i, trending, trend_up):
    buy = sell = 0
    why = []
    r = _f(s["rsi"], i)
    r_p = _f(s["rsi"], i - 3)
    if _nan(r):
        return 0, 0, ["RSI : indisponible"], r
    if trending:
        if trend_up:
            if 50 <= r <= 75:
                buy += 1
                why.append(f"RSI(14) {r:.1f} : élan acheteur sain en tendance haussière (50-75)")
            elif r > 75:
                why.append(f"RSI(14) {r:.1f} : tendance haussière mais SURCHAUFFE (> 75) — entrée tardive, 0 point")
            elif 35 <= r < 50 and r > r_p:
                buy += 1
                why.append(f"RSI(14) {r:.1f} : repli dans une tendance haussière, l'élan repart (RSI en hausse)")
            else:
                why.append(f"RSI(14) {r:.1f} : sous 50 en tendance haussière — élan fragile, 0 point")
        else:
            if 25 <= r <= 50:
                sell += 1
                why.append(f"RSI(14) {r:.1f} : élan vendeur sain en tendance baissière (25-50)")
            elif r < 25:
                why.append(f"RSI(14) {r:.1f} : tendance baissière mais SURVENTE (< 25) — entrée tardive, 0 point")
            elif 50 < r <= 65 and r < r_p:
                sell += 1
                why.append(f"RSI(14) {r:.1f} : rebond dans une tendance baissière, l'élan repart (RSI en baisse)")
            else:
                why.append(f"RSI(14) {r:.1f} : au-dessus de 50 en tendance baissière — élan fragile, 0 point")
    else:  # range : retours à la moyenne
        if r < 30:
            buy += 2
            why.append(f"RSI(14) {r:.1f} : survente (< 30) en range — rebond probable")
        elif r < 40:
            buy += 1
            why.append(f"RSI(14) {r:.1f} : zone basse (< 40) en range — biais acheteur")
        elif r > 70:
            sell += 2
            why.append(f"RSI(14) {r:.1f} : surachat (> 70) en range — repli probable")
        elif r > 60:
            sell += 1
            why.append(f"RSI(14) {r:.1f} : zone haute (> 60) en range — biais vendeur")
        else:
            why.append(f"RSI(14) {r:.1f} : zone neutre (40-60)")
    return buy, sell, why, r


def _score_bollinger(s, i, close, trending, trend_up):
    buy = sell = 0
    up, low, mid = _f(s["bb_upper"], i), _f(s["bb_lower"], i), _f(s["bb_mid"], i)
    if any(_nan(x) for x in (up, low, mid)):
        return 0, 0, ["Bollinger : indisponible"], float("nan")
    rng = max(up - low, 1e-12)
    pos = (close - low) / rng          # 0 = bande basse, 1 = bande haute
    if trending:
        if trend_up and close > mid and pos >= 0.6:
            buy += 1
            why = "Bollinger : prix dans la moitié haute des bandes en tendance haussière — pression acheteuse"
        elif (not trend_up) and close < mid and pos <= 0.4:
            sell += 1
            why = "Bollinger : prix dans la moitié basse des bandes en tendance baissière — pression vendeuse"
        else:
            why = "Bollinger : prix mal placé par rapport à la tendance — 0 point"
    else:
        if pos <= 0.05:
            buy += 1
            why = "Bollinger : prix collé à la bande basse en range — retour vers la moyenne probable"
        elif pos >= 0.95:
            sell += 1
            why = "Bollinger : prix collé à la bande haute en range — retour vers la moyenne probable"
        else:
            why = "Bollinger : prix au centre des bandes — pas d'extrême"
    return buy, sell, [why], pos


def _score_stochastic(s, i, trending, trend_up):
    k_now, d_now = _f(s["stoch_k"], i), _f(s["stoch_d"], i)
    for j in range(max(1, i - 2), i + 1):
        k, d = _f(s["stoch_k"], j), _f(s["stoch_d"], j)
        k_p, d_p = _f(s["stoch_k"], j - 1), _f(s["stoch_d"], j - 1)
        if any(_nan(x) for x in (k, d, k_p, d_p)):
            continue
        if k_p <= d_p and k > d and k < 25 and (not trending or trend_up):
            return 1, 0, [f"Stochastique : croisement haussier en zone basse (%K={k:.0f}) "
                          "sur les 3 dernières bougies — retournement depuis le plancher"]
        if k_p >= d_p and k < d and k > 75 and (not trending or not trend_up):
            return 0, 1, [f"Stochastique : croisement baissier en zone haute (%K={k:.0f}) "
                          "sur les 3 dernières bougies — retournement depuis le plafond"]
    if _nan(k_now):
        return 0, 0, ["Stochastique : indisponible"]
    return 0, 0, [f"Stochastique : %K={k_now:.0f}, %D={d_now:.0f} — pas de croisement marquant"]


def _score_patterns(df_view, atr_v, trending, trend_up):
    buy = sell = 0
    why = []
    found = patterns.detect_patterns(df_view, atr_val=atr_v if not _nan(atr_v) else None)
    for f in found:
        pts = f["points"]
        # contre une tendance confirmée, une figure de retournement ne compte pas
        if trending and pts and ((f["side"] == "buy" and not trend_up)
                                 or (f["side"] == "sell" and trend_up)):
            pts = 0
            why.append(f"Chandelier — {f['name']} : contre la tendance confirmée, ignoré (0 point)")
            continue
        if f["side"] == "buy":
            buy += pts
        elif f["side"] == "sell":
            sell += pts
        why.append(f"Chandelier — {f['name']} : {f['text']}")
    buy, sell = min(buy, 2), min(sell, 2)
    if not found:
        why.append("Chandelier : aucune figure notable sur la dernière bougie")
    return buy, sell, why, found


def _score_reversion(s, i, close, atr_v):
    """Range : écart du prix à l'EMA21, mesuré en ATR (étirement = retour probable)."""
    e21 = _f(s["ema21"], i)
    if _nan(e21) or _nan(atr_v) or atr_v <= 0:
        return 0, 0, ["Écart à la moyenne : indisponible"]
    z = (close - e21) / atr_v
    if z <= -1.0:
        pts = 3 if z <= -2.0 else (2 if z <= -1.5 else 1)
        return pts, 0, [f"Écart à la moyenne : prix {abs(z):.1f} ATR SOUS l'EMA21 — étiré à la baisse, "
                        f"retour vers la moyenne probable ({pts}/3)"]
    if z >= 1.0:
        pts = 3 if z >= 2.0 else (2 if z >= 1.5 else 1)
        return 0, pts, [f"Écart à la moyenne : prix {z:.1f} ATR AU-DESSUS de l'EMA21 — étiré à la hausse, "
                        f"retour vers la moyenne probable ({pts}/3)"]
    return 0, 0, [f"Écart à la moyenne : prix à {z:+.1f} ATR de l'EMA21 — pas d'étirement"]


def _score_volume(s, i, row):
    v = _f(s["vol_ratio"], i)
    if _nan(v):
        return 0, 0, ["Volume : indisponible sur ce marché — ignoré"]
    if v >= 1.5:
        if row["close"] > row["open"]:
            return 1, 0, [f"Volume : x{v:.1f} la moyenne sur une bougie haussière — achat confirmé par les volumes"]
        if row["close"] < row["open"]:
            return 0, 1, [f"Volume : x{v:.1f} la moyenne sur une bougie baissière — vente confirmée par les volumes"]
    return 0, 0, [f"Volume : x{v:.1f} la moyenne — pas de pic de confirmation"]


# ------------------------------------------------------------------
# Tendance de fond (utilisée par le contrôle multi-timeframe)
# ------------------------------------------------------------------
def _trend_label(s, i, close):
    e21, e50 = _f(s["ema21"], i), _f(s["ema50"], i)
    if _nan(e50):
        return "neutre"
    if close > e50 and e21 > e50:
        return "haussière"
    if close < e50 and e21 < e50:
        return "baissière"
    return "neutre"


# ------------------------------------------------------------------
# Analyse
# ------------------------------------------------------------------
def analyze(df: pd.DataFrame, decimals: int | None = None) -> dict:
    """Analyse complète de la dernière bougie fermée de `df`."""
    if len(df) < MIN_BARS:
        raise InsufficientData(f"{len(df)} bougies, {MIN_BARS} requises")
    s = indicators.compute_series(df)
    return analyze_at(df, s, len(df) - 1, decimals)


def analyze_at(df: pd.DataFrame, s: dict, i: int, decimals: int | None = None) -> dict:
    """Analyse à la bougie d'indice i, en n'utilisant QUE les bougies <= i.

    `s` = indicators.compute_series(df) calculé une fois (le backtest le
    réutilise pour aller vite : les indicateurs sont causaux, donc identiques)."""
    row = df.iloc[i]
    close = float(row["close"])
    atr_v = _f(s["atr"], i)
    adx_v, pdi, mdi = _f(s["adx"], i), _f(s["plus_di"], i), _f(s["minus_di"], i)
    trending = (not _nan(adx_v)) and adx_v >= settings.ADX_TREND
    trend_up = (not _nan(pdi) and not _nan(mdi)) and pdi > mdi
    regime = "tendance" if trending else "range"

    b_tr, s_tr, r_tr = _score_trend(s, i, close)
    b_mc, s_mc, r_mc = _score_macd(s, i)
    b_ax, s_ax, r_ax = _score_adx(adx_v, pdi, mdi, trending)
    b_rs, s_rs, r_rs, rsi_v = _score_rsi(s, i, trending, trend_up)
    b_bb, s_bb, r_bb, bb_pos = _score_bollinger(s, i, close, trending, trend_up)
    b_st, s_st, r_st = _score_stochastic(s, i, trending, trend_up)
    b_pt, s_pt, r_pt, found = _score_patterns(df.iloc[: i + 1], atr_v, trending, trend_up)
    b_vo, s_vo, r_vo = _score_volume(s, i, row)

    if trending:
        regime_max = MAX_POINTS
        b_zs = s_zs = 0
        r_zs = []
    else:
        # Range : EMA et MACD suivent le mouvement -> affichés mais NON comptés
        regime_max = MAX_POINTS_RANGE
        r_tr = ["(info, non compté en range) " + t for t in r_tr]
        r_mc = ["(info, non compté en range) " + t for t in r_mc]
        b_tr = s_tr = b_mc = s_mc = 0
        b_zs, s_zs, r_zs = _score_reversion(s, i, close, atr_v)

    buy = b_tr + b_mc + b_ax + b_zs + b_rs + b_bb + b_st + b_pt + b_vo
    sell = s_tr + s_mc + s_ax + s_zs + s_rs + s_bb + s_st + s_pt + s_vo
    reasons = r_tr + r_mc + r_ax + r_zs + r_rs + r_bb + r_st + r_pt + r_vo

    scale = regime_max / MAX_POINTS
    min_points = max(3, round(settings.SIGNAL_MIN_POINTS * scale))
    min_margin = max(2, round(settings.SIGNAL_MIN_MARGIN * scale))

    total = buy + sell
    dominant = max(buy, sell)
    margin = abs(buy - sell)
    agreement = round(100 * dominant / total) if total else 50

    raw_direction = "ATTENDRE"
    if buy != sell:
        raw_direction = "ACHAT" if buy > sell else "VENTE"
        if (dominant < min_points or margin < min_margin
                or agreement < settings.SIGNAL_MIN_CONFIDENCE):
            raw_direction = "ATTENDRE"

    # ---------------- filtres de qualité -> peuvent annuler le signal
    direction = raw_direction
    blocked_by = None
    if atr_v is None or _nan(atr_v) or atr_v <= 0:
        atr_v = float(row["high"] - row["low"]) or close * 0.002
    atr_pct = atr_v / close * 100 if close else 0.0
    e21 = _f(s["ema21"], i)
    if direction != "ATTENDRE":
        if atr_pct < settings.MIN_ATR_PCT:
            blocked_by = (f"marché trop calme (ATR {atr_pct:.3f}% < {settings.MIN_ATR_PCT:g}%) : "
                          "le spread mangerait le gain visé")
        elif (settings.MAX_STRETCH_ATR > 0 and direction == "ACHAT"
              and (close - e21) > settings.MAX_STRETCH_ATR * atr_v):
            blocked_by = (f"prix trop étiré au-dessus de l'EMA21 (> {settings.MAX_STRETCH_ATR:g} ATR) : "
                          "poursuivre le mouvement est risqué, attends un repli")
        elif (settings.MAX_STRETCH_ATR > 0 and direction == "VENTE"
              and (e21 - close) > settings.MAX_STRETCH_ATR * atr_v):
            blocked_by = (f"prix trop étiré sous l'EMA21 (> {settings.MAX_STRETCH_ATR:g} ATR) : "
                          "poursuivre le mouvement est risqué, attends un rebond")
        elif direction == "ACHAT" and not _nan(rsi_v) and rsi_v >= 78:
            blocked_by = f"RSI {rsi_v:.0f} >= 78 : entrée acheteuse trop tardive"
        elif direction == "VENTE" and not _nan(rsi_v) and rsi_v <= 22:
            blocked_by = f"RSI {rsi_v:.0f} <= 22 : entrée vendeuse trop tardive"
        if blocked_by:
            direction = "ATTENDRE"
            reasons.append(f"FILTRE : signal {raw_direction} annulé — {blocked_by}")

    lean = raw_direction if raw_direction != "ATTENDRE" else ("ACHAT" if buy >= sell else "VENTE")
    levels = build_levels(close, atr_v, 1 if lean == "ACHAT" else -1, decimals)

    if buy - sell >= 2:
        bias = "ACHAT"
    elif sell - buy >= 2:
        bias = "VENTE"
    else:
        bias = "NEUTRE"

    stoch_k, stoch_d = _f(s["stoch_k"], i), _f(s["stoch_d"], i)
    vol_ratio = _f(s["vol_ratio"], i)
    indicators_out = {
        "rsi": rsi_v, "ema9": _f(s["ema9"], i), "ema21": e21, "ema50": _f(s["ema50"], i),
        "macd": _f(s["macd_line"], i), "macd_signal": _f(s["macd_signal"], i),
        "macd_hist": _f(s["macd_hist"], i),
        "bb_pos": bb_pos, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "atr": atr_v, "atr_pct": atr_pct, "adx": adx_v, "vol_ratio": vol_ratio,
        "buy_points": buy, "sell_points": sell,
        "tp_pct": levels["tp_pct"], "sl_pct": levels["sl_pct"], "rr": levels["rr"],
    }
    bar_time = pd.Timestamp(row["time"])
    return {
        "direction": direction, "raw_direction": raw_direction,
        "blocked_by": blocked_by,
        "confidence": agreement, "score": dominant, "max_points": regime_max,
        "buy_points": buy, "sell_points": sell,
        "entry": levels["entry"], "tp": levels["tp"], "sl": levels["sl"],
        "rr": levels["rr"], "atr": atr_v, "atr_pct": atr_pct,
        "regime": regime, "adx": adx_v,
        "trend": _trend_label(s, i, close), "bias": bias,
        "bar_time": bar_time.isoformat(),
        "reasons": reasons, "patterns": found, "indicators": indicators_out,
    }
