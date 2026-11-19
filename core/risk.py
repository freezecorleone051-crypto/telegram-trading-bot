"""
Gestion du risque : niveaux TP/SL calibrés sur l'ATR, ratio risque/rendement
et suggestion de taille de position.
"""
from config import settings
from core import indicators


def build_levels(entry: float, atr_val: float, ref: int) -> dict:
    """ref = +1 pour un scénario haussier, -1 pour baissier.

    TP = entrée +/- TP_ATR x ATR ; SL = entrée -/+ SL_ATR x ATR
    Renvoie entry, tp, sl, rr (risque/rendement), tp_pct, sl_pct.
    """
    entry = indicators.smart_round(entry)
    tp = indicators.smart_round(entry + ref * settings.TP_ATR * atr_val)
    sl = indicators.smart_round(entry - ref * settings.SL_ATR * atr_val)
    tp_pct = abs(tp - entry) / entry * 100
    sl_pct = abs(entry - sl) / entry * 100
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    return {"entry": entry, "tp": tp, "sl": sl, "rr": rr,
            "tp_pct": tp_pct, "sl_pct": sl_pct}


def position_hint(entry: float, sl: float) -> str | None:
    """Suggestion de quantité pour risquer RISK_PCT % de BALANCE (si réglée)."""
    if settings.BALANCE <= 0:
        return None
    risk_amount = settings.BALANCE * settings.RISK_PCT / 100.0
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return None
    qty = risk_amount / risk_per_unit
    return (f"Si tu risques {settings.RISK_PCT:g}% de {settings.BALANCE:g} "
            f"(= {risk_amount:g}), taille conseillée : ~{qty:.4g} unités.")
