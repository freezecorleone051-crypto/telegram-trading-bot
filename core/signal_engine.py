"""
Moteur de signal : combine tous les modules d'analyse en un verdict.

Modules et points maximum (par côté) :
  - Tendance EMA 9/21           : 2 (+2 en cas de croisement récent)
  - RSI(14)                     : 2
  - MACD(12,26,9)               : 2
  - Bandes de Bollinger(20,2)   : 1
  - Stochastique(14,3)          : 1
  - Chandeliers japonais        : 2 (limité)
                                    -----
                                    max 12 par côté

Un signal ACHAT/VENTE n'est annoncé que si :
  confiance >= SIGNAL_MIN_CONFIDENCE  ET  écart de points >= SIGNAL_MIN_MARGIN
  ET points du côté dominant >= SIGNAL_MIN_POINTS
Sinon le verdict est ATTENDRE (honnêteté avant tout : mieux vaut ne rien
dire que d'inventer un signal).
"""
import pandas as pd

from config import settings
from core import indicators, patterns

DIRECTIONS = ("ACHAT", "VENTE", "ATTENDRE")


def _score_ema(df, s, close_last):
    buy = sell = 0
    reasons = []
    e9, e21 = float(s["ema9"].iloc[-1]), float(s["ema21"].iloc[-1])
    e9_p, e21_p = float(s["ema9"].iloc[-2]), float(s["ema21"].iloc[-2])
    if e9 > e21 and close_last > e21:
        buy += 2
        reasons.append(f"EMA 9/21 : haussier — EMA9 ({indicators.smart_round(e9)}) "
                       f"au-dessus de l'EMA21 ({indicators.smart_round(e21)}) et prix au-dessus des deux")
    elif e9 < e21 and close_last < e21:
        sell += 2
        reasons.append(f"EMA 9/21 : baissier — EMA9 ({indicators.smart_round(e9)}) "
                       f"en dessous de l'EMA21 ({indicators.smart_round(e21)}) et prix en dessous des deux")
    else:
        reasons.append("EMA 9/21 : prix entre les deux moyennes — tendance hésitante")
    if e9_p <= e21_p and e9 > e21:
        buy += 2
        reasons.append("Croisement haussier EMA9/EMA21 sur les dernières bougies")
    elif e9_p >= e21_p and e9 < e21:
        sell += 2
        reasons.append("Croisement baissier EMA9/EMA21 sur les dernières bougies")
    return buy, sell, reasons


def _score_rsi(s):
    buy = sell = 0
    reasons = []
    r = float(s["rsi"].iloc[-1])
    r_p = float(s["rsi"].iloc[-3]) if len(s["rsi"]) > 3 else r
    if r < 30:
        buy += 2
        reasons.append(f"RSI(14) : {r:.1f} — survente (< 30), rebond probable")
    elif r < 45:
        buy += 1
        reasons.append(f"RSI(14) : {r:.1f} — léger biais acheteur (sous 45)")
    elif r > 70:
        sell += 2
        reasons.append(f"RSI(14) : {r:.1f} — surachat (> 70), repli probable")
    elif r > 55:
        sell += 1
        reasons.append(f"RSI(14) : {r:.1f} — léger biais vendeur (au-dessus de 55)")
    else:
        reasons.append(f"RSI(14) : {r:.1f} — zone neutre (45-55)")
    if r > r_p + 5:
        reasons.append(f"RSI en nette hausse depuis 3 bougies ({r_p:.1f} -> {r:.1f})")
    elif r < r_p - 5:
        reasons.append(f"RSI en nette baisse depuis 3 bougies ({r_p:.1f} -> {r:.1f})")
    return buy, sell, reasons, r


def _score_macd(s):
    buy = sell = 0
    reasons = []
    m, sg = float(s["macd_line"].iloc[-1]), float(s["macd_signal"].iloc[-1])
    h_now, h_prev = float(s["macd_hist"].iloc[-1]), float(s["macd_hist"].iloc[-2])
    if m > sg and h_now > h_prev:
        buy += 2
        reasons.append("MACD : ligne au-dessus du signal ET histogramme en hausse — "
                       "élan haussier qui se renforce")
    elif m > sg:
        buy += 1
        reasons.append("MACD : croisement haussier en cours mais élan qui faiblit")
    elif m < sg and h_now < h_prev:
        sell += 2
        reasons.append("MACD : ligne en dessous du signal ET histogramme en baisse — "
                       "élan baissier qui se renforce")
    else:
        sell += 1
        reasons.append("MACD : croisement baissier en cours mais élan qui faiblit")
    return buy, sell, reasons, m, sg


def _score_bollinger(s, close_last):
    buy = sell = 0
    reasons = []
    up = float(s["bb_upper"].iloc[-1])
    low = float(s["bb_lower"].iloc[-1])
    mid = float(s["bb_mid"].iloc[-1])
    rng = max(up - low, 1e-12)
    pos = (close_last - low) / rng  # 0 = bande basse, 1 = bande haute
    if pos <= 0.05:
        buy += 1
        reasons.append("Bollinger : prix collé à la bande basse — extrême baissier, "
                       "retour vers la moyenne probable")
    elif pos >= 0.95:
        sell += 1
        reasons.append("Bollinger : prix collé à la bande haute — extrême haussier, "
                       "retour vers la moyenne probable")
    elif close_last > mid and pos >= 0.6:
        buy += 1
        reasons.append("Bollinger : prix dans la moitié haute des bandes — pression acheteuse")
    elif close_last < mid and pos <= 0.4:
        sell += 1
        reasons.append("Bollinger : prix dans la moitié basse des bandes — pression vendeuse")
    else:
        reasons.append("Bollinger : prix au centre des bandes — pas d'extrême")
    return buy, sell, reasons, pos


def _score_stochastic(s):
    buy = sell = 0
    reasons = []
    k, d = float(s["stoch_k"].iloc[-1]), float(s["stoch_d"].iloc[-1])
    k_p, d_p = float(s["stoch_k"].iloc[-2]), float(s["stoch_d"].iloc[-2])
    if k_p <= d_p and k > d and k < 25:
        buy += 1
        reasons.append(f"Stochastique : croisement haussier en zone basse (%K={k:.0f}) — "
                       "retournement depuis le plancher")
    elif k_p >= d_p and k < d and k > 75:
        sell += 1
        reasons.append(f"Stochastique : croisement baissier en zone haute (%K={k:.0f}) — "
                       "retournement depuis le plafond")
    else:
        reasons.append(f"Stochastique : %K={k:.0f}, %D={d:.0f} — pas de croisement marquant")
    return buy, sell, reasons


def _score_patterns(df):
    buy = sell = 0
    reasons = []
    found = patterns.detect_patterns(df)
    for f in found:
        if f["side"] == "buy":
            buy += f["points"]
        elif f["side"] == "sell":
            sell += f["points"]
        reasons.append(f"Chandelier — {f['name']} : {f['text']}")
    # Limitation : les figures comptent au maximum 2 points par côté
    buy, sell = min(buy, 2), min(sell, 2)
    if not found:
        reasons.append("Chandelier : aucune figure notable sur les dernières bougies")
    return buy, sell, reasons, found


# ------------------------------------------------------------------
# Analyse principale
# ------------------------------------------------------------------
def analyze(df: pd.DataFrame) -> dict:
    """
    Analyse complète. Renvoie un dict :
    direction ('ACHAT'|'VENTE'|'ATTENDRE'), confidence (0-100),
    buy_points, sell_points, entry, tp, sl, rr, atr,
    reasons (liste), patterns (liste de dicts), indicators (valeurs brutes).
    """
    close_last = float(df["close"].iloc[-1])
    s = indicators.compute_series(df)

    b_ema, s_ema, r_ema = _score_ema(df, s, close_last)
    b_rsi, s_rsi, r_rsi, r_val = _score_rsi(s)
    b_macd, s_macd, r_macd, m_val, sg_val = _score_macd(s)
    b_bb, s_bb, r_bb, bb_pos = _score_bollinger(s, close_last)
    b_st, s_st, r_st = _score_stochastic(s)
    b_pat, s_pat, r_pat, found = _score_patterns(df)

    buy = b_ema + b_rsi + b_macd + b_bb + b_st + b_pat
    sell = s_ema + s_rsi + s_macd + s_bb + s_st + s_pat
    reasons = r_ema + r_rsi + r_macd + r_bb + r_st + r_pat

    total = buy + sell
    if total == 0:
        direction, confidence = "ATTENDRE", 50
    else:
        confidence = round(100 * max(buy, sell) / total)
        direction = "ACHAT" if buy > sell else ("VENTE" if sell > buy else "ATTENDRE")
        if (confidence < settings.SIGNAL_MIN_CONFIDENCE
                or abs(buy - sell) < settings.SIGNAL_MIN_MARGIN
                or max(buy, sell) < settings.SIGNAL_MIN_POINTS):
            direction = "ATTENDRE"

    atr_val = float(s["atr"].iloc[-1])
    if pd.isna(atr_val) or atr_val <= 0:
        atr_val = float(df["high"].iloc[-1] - df["low"].iloc[-1]) or close_last * 0.002

    from core.risk import build_levels  # import local pour éviter la boucle
    ref = 1 if (direction == "ACHAT" or (direction == "ATTENDRE" and buy >= sell)) else -1
    levels = build_levels(close_last, atr_val, ref)

    indicators_out = {
        "rsi": r_val, "ema9": float(s["ema9"].iloc[-1]),
        "ema21": float(s["ema21"].iloc[-1]),
        "macd": m_val, "macd_signal": sg_val,
        "macd_hist": float(s["macd_hist"].iloc[-1]),
        "bb_pos": bb_pos, "stoch_k": float(s["stoch_k"].iloc[-1]),
        "stoch_d": float(s["stoch_d"].iloc[-1]),
        "atr": atr_val, "buy_points": buy, "sell_points": sell,
        "tp_pct": levels["tp_pct"], "sl_pct": levels["sl_pct"],
        "rr": levels["rr"],
    }

    return {
        "direction": direction, "confidence": confidence,
        "buy_points": buy, "sell_points": sell,
        "entry": levels["entry"], "tp": levels["tp"], "sl": levels["sl"],
        "rr": levels["rr"], "atr": atr_val,
        "reasons": reasons, "patterns": found, "indicators": indicators_out,
    }
