"""
Gestion du risque : niveaux TP/SL calibrés sur l'ATR, ratio risque/rendement
et suggestion de taille de position.
"""
import math

from config import settings
from core import indicators


def build_levels(entry: float, atr_val: float, ref: int,
                 decimals: int | None = None) -> dict:
    """ref = +1 pour un scénario haussier, -1 pour baissier.

    TP = entrée +/- TP_ATR x ATR ; SL = entrée -/+ SL_ATR x ATR
    Renvoie entry, tp, sl, rr (risque/rendement), tp_pct, sl_pct.

    Le ratio R:R et les pourcentages sont calculés sur les valeurs EXACTES
    (la v1 les calculait sur des valeurs arrondies, ce qui faussait le ratio
    de plusieurs % sur les petits mouvements). `decimals` impose la précision
    d'affichage (ex. 5 pour l'EUR/USD, 3 pour l'USD/JPY, 2 pour une action).
    """
    tp_raw = entry + ref * settings.TP_ATR * atr_val
    sl_raw = entry - ref * settings.SL_ATR * atr_val
    risk = abs(entry - sl_raw)
    reward = abs(tp_raw - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    return {
        "entry": indicators.smart_round(entry, decimals),
        "tp": indicators.smart_round(tp_raw, decimals),
        "sl": indicators.smart_round(sl_raw, decimals),
        "rr": rr,
        "tp_pct": reward / entry * 100 if entry else 0.0,
        "sl_pct": risk / entry * 100 if entry else 0.0,
    }


def position_size(entry: float, sl: float, market: str | None = None):
    """Taille de position pour risquer RISK_PCT % de BALANCE.

    Renvoie un dict {qty, risk_amount, capped} ou None si BALANCE n'est pas
    réglée. La taille est plafonnée par le capital x LEVIER (par défaut sans
    levier : on ne peut pas acheter plus que ce que l'on possède). Pour les
    actions, la quantité est arrondie à l'entier inférieur."""
    if settings.BALANCE <= 0:
        return None
    risk_amount = settings.BALANCE * settings.RISK_PCT / 100.0
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0 or entry <= 0:
        return None
    qty = risk_amount / risk_per_unit
    max_qty = settings.BALANCE * settings.LEVERAGE / entry
    capped = qty > max_qty
    qty = min(qty, max_qty)
    if market in ("stocks", "etf"):
        qty = math.floor(qty)
    return {"qty": qty, "risk_amount": risk_amount, "capped": capped}


def position_hint(entry: float, sl: float, market: str | None = None) -> str | None:
    """Suggestion de quantité en une phrase (ou None si BALANCE = 0)."""
    p = position_size(entry, sl, market)
    if p is None:
        return None
    unit = "actions" if market in ("stocks", "etf") else "unités"
    txt = (f"Si tu risques {settings.RISK_PCT:g}% de {settings.BALANCE:g} "
           f"(= {p['risk_amount']:g}), taille conseillée : ~{p['qty']:.4g} {unit}.")
    if p["capped"]:
        txt += (" (plafonnée par ton capital"
                + ("" if settings.LEVERAGE <= 1 else f" x levier {settings.LEVERAGE:g}")
                + " : le stop est plus large que le risque voulu.)")
    return txt
