"""
Backtest « walk-forward » honnête du moteur de signal.

Principe : on rejoue l'historique bougie par bougie. À la clôture de la
bougie i, le moteur ne voit QUE les bougies <= i (aucune fuite du futur,
vérifié par tests/test_no_lookahead.py). S'il émet un signal, on suppose une
entrée au cours de clôture, puis on regarde les bougies SUIVANTES pour savoir
si le TP ou le SL est touché en premier :
  - SL et TP touchés dans la même bougie -> perte (hypothèse conservatrice,
    identique au suivi en direct de /stats) ;
  - ni l'un ni l'autre après `max_bars` bougies -> sortie à la clôture
    (« expired ») ;
  - un seul trade à la fois (pas de chevauchement).

Résultats en multiples de R (R = le risque pris : perte = -1R, gain = +RR) :
espérance, facteur de profit, drawdown, série perdante maximale, et
comparaison avec « acheter et garder » sur la même période.

Ce que le backtest NE peut PAS garantir : que le futur ressemblera au passé.
Un bon résultat passé n'est jamais une promesse ; un mauvais résultat est en
revanche une vraie information (le bot n'a pas d'avantage sur cet actif).
"""
import math

import numpy as np
import pandas as pd

from config import settings
from core import indicators, signal_engine
from data import markets
from utils.helpers import wilson_interval

# Durée maximale d'un trade (en bougies) avant sortie forcée
DEFAULT_MAX_BARS = {"1m": 60, "5m": 48, "15m": 32, "1h": 36, "4h": 24, "1d": 15, "1w": 8}


# Coût aller-retour supposé (spread + commissions), en % du prix, par famille.
# Valeurs prudentes pour un particulier ; à ajuster selon ton courtier.
DEFAULT_COST_PCT = {"crypto": 0.10, "forex": 0.02, "otc": 0.0, "stocks": 0.03,
                    "etf": 0.02, "indices": 0.02, "commodities": 0.04}


# ------------------------------------------------------------------
# Simulation d'un trade
# ------------------------------------------------------------------
def simulate_trade(highs, lows, closes, i, direction, entry, tp, sl, max_bars):
    """Parcourt les bougies i+1... Renvoie (statut, indice_sortie, R_brut).
    statut : win | loss | expired | open (pas assez de données pour conclure)."""
    n = len(closes)
    sign = 1 if direction == "ACHAT" else -1
    risk = abs(entry - sl)
    if risk <= 0:
        return "open", None, 0.0
    last = min(n - 1, i + max_bars)
    for j in range(i + 1, last + 1):
        if sign > 0:
            hit_sl, hit_tp = lows[j] <= sl, highs[j] >= tp
        else:
            hit_sl, hit_tp = highs[j] >= sl, lows[j] <= tp
        if hit_sl:                      # SL prioritaire : hypothèse conservatrice
            return "loss", j, -1.0
        if hit_tp:
            return "win", j, abs(tp - entry) / risk
    if last - i >= max_bars:            # délai écoulé : sortie à la clôture
        return "expired", last, sign * (closes[last] - entry) / risk
    return "open", None, 0.0


def _ns(times: pd.Series) -> np.ndarray:
    """Instants en nanosecondes UTC (indépendant de la résolution pandas)."""
    t = times.dt.tz_convert("UTC").dt.tz_localize(None)
    return t.to_numpy(dtype="datetime64[ns]").astype("int64")


# ------------------------------------------------------------------
# Tendance du timeframe supérieur (rejouée sans fuite du futur)
# ------------------------------------------------------------------
def _htf_trend_provider(df: pd.DataFrame, base_tf: str, market: str):
    """Renvoie f(i) -> -1/0/+1 : tendance du timeframe supérieur IMMÉDIAT,
    calculée uniquement avec des bougies supérieures déjà COMPLÈTES à l'instant i.
    Renvoie None si le timeframe de base n'a pas de timeframe supérieur utilisable."""
    chain = markets.mtf_map_for(market).get(base_tf, [base_tf])
    higher = [t for t in chain[1:] if t != base_tf]
    if not higher:
        return None
    htf = higher[0]
    base_s, htf_s = markets.TF_SECONDS[base_tf], markets.TF_SECONDS[htf]
    if htf_s % base_s != 0 or htf_s <= base_s:
        return None
    origin = "start" if market in markets.SESSION_MARKETS else "epoch"
    d = df.assign(time=df["time"].dt.tz_convert("UTC")).set_index("time")
    agg = d.resample(f"{htf_s}s", origin=origin, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna(subset=["open", "close"]).reset_index()
    if len(agg) < signal_engine.MIN_BARS:
        return None
    s = indicators.compute_series(agg)
    labels = np.array([{"haussière": 1, "baissière": -1, "neutre": 0}[
        signal_engine._trend_label(s, j, float(agg["close"].iloc[j]))]
        for j in range(len(agg))])
    ends = _ns(agg["time"] + pd.Timedelta(seconds=htf_s))
    base_close = _ns(df["time"] + pd.Timedelta(seconds=base_s))
    # dernier indice htf dont la fermeture est <= fermeture de la bougie de base i
    idx = np.searchsorted(ends, base_close, side="right") - 1

    def provider(i: int) -> int:
        j = idx[i]
        return int(labels[j]) if j >= 0 else 0
    return provider


# ------------------------------------------------------------------
# Backtest
# ------------------------------------------------------------------
def run_backtest(df: pd.DataFrame, timeframe: str, market: str = "crypto",
                 cost_pct: float = 0.0, max_bars: int | None = None,
                 warmup: int = 80, use_mtf: bool = False,
                 decimals: int | None = None, engine=None) -> dict:
    """Rejoue `df` (bougies FERMÉES) et renvoie {"trades": [...], "summary": {...}}.

    cost_pct : coût aller-retour en % du prix (spread + commissions), retiré
               de chaque trade (en R : cost_pct / distance du SL en %).
    use_mtf  : applique le filtre de tendance du timeframe supérieur (mode strict)
               aux signaux de tendance (comme en direct).
    engine   : fonction (df, i) -> signal, pour comparer un autre moteur.
    """
    df = df.reset_index(drop=True)
    n = len(df)
    max_bars = max_bars or DEFAULT_MAX_BARS.get(timeframe, 40)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)

    if engine is None:
        series = indicators.compute_series(df)

        def engine(frame, i):
            return signal_engine.analyze_at(frame, series, i, decimals)
    htf = _htf_trend_provider(df, timeframe, market) if use_mtf else None

    trades, i = [], max(warmup, signal_engine.MIN_BARS)
    while i < n - 1:
        sig = engine(df, i)
        direction = sig["direction"]
        if direction == "ATTENDRE":
            i += 1
            continue
        # Le filtre de timeframe supérieur ne s'applique qu'aux signaux de TENDANCE :
        # un retour à la moyenne (range) va par nature contre le mouvement court.
        if htf is not None and sig.get("regime") == "tendance":
            sign = 1 if direction == "ACHAT" else -1
            if htf(i) == -sign:       # timeframe supérieur contre : filtré
                i += 1
                continue
        entry, tp, sl = sig["entry"], sig["tp"], sig["sl"]
        status, j, r_gross = simulate_trade(highs, lows, closes, i, direction,
                                            entry, tp, sl, max_bars)
        if status == "open":          # fin des données : on ne peut pas conclure
            break
        sl_pct = abs(entry - sl) / entry * 100
        r_net = r_gross - (cost_pct / sl_pct if sl_pct > 0 else 0.0)
        trades.append({
            "bar_time": str(df["time"].iloc[i]), "direction": direction,
            "entry": entry, "tp": tp, "sl": sl, "status": status,
            "r": r_net, "r_gross": r_gross, "bars": j - i,
            "regime": sig.get("regime", ""), "score": sig.get("score", 0),
        })
        i = j       # un trade à la fois : on cherche le signal suivant à la sortie
    first, last = float(closes[0]), float(closes[-1])
    summary = summarize(trades, n_bars=n - max(warmup, signal_engine.MIN_BARS),
                        buy_hold_pct=(last / first - 1) * 100,
                        cost_pct=cost_pct, timeframe=timeframe)
    return {"trades": trades, "summary": summary}


# ------------------------------------------------------------------
# Statistiques
# ------------------------------------------------------------------
def summarize(trades: list, n_bars: int = 0, buy_hold_pct: float | None = None,
              cost_pct: float = 0.0, timeframe: str = "") -> dict:
    n = len(trades)
    wins = [t for t in trades if t["status"] == "win"]
    losses = [t for t in trades if t["status"] == "loss"]
    expired = [t for t in trades if t["status"] == "expired"]
    rs = np.array([t["r"] for t in trades], dtype=float)
    out = {"trades": n, "wins": len(wins), "losses": len(losses),
           "expired": len(expired), "cost_pct": cost_pct, "timeframe": timeframe,
           "buy_hold_pct": buy_hold_pct, "bars": n_bars}
    decided = len(wins) + len(losses)
    out["win_rate"] = len(wins) / decided if decided else None
    out["win_rate_ci"] = wilson_interval(len(wins), decided) if decided else None
    out["total_r"] = float(rs.sum()) if n else 0.0
    out["avg_r"] = float(rs.mean()) if n else 0.0
    gain, loss = float(rs[rs > 0].sum()) if n else 0.0, float(-rs[rs < 0].sum()) if n else 0.0
    out["profit_factor"] = (gain / loss) if loss > 0 else (float("inf") if gain > 0 else None)
    if n:
        equity = np.cumsum(rs)
        peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
        out["max_drawdown_r"] = float((peak - equity).max())
        streak = best = 0
        for r in rs:
            streak = streak + 1 if r < 0 else 0
            best = max(best, streak)
        out["max_losing_streak"] = best
        out["avg_bars"] = float(np.mean([t["bars"] for t in trades]))
    else:
        out.update({"max_drawdown_r": 0.0, "max_losing_streak": 0, "avg_bars": 0.0})
    avg_rr = np.mean([abs(t["tp"] - t["entry"]) / max(abs(t["entry"] - t["sl"]), 1e-12)
                      for t in trades]) if n else None
    out["breakeven_win_rate"] = (1 / (1 + avg_rr)) if avg_rr else None
    out["by_direction"] = {
        d: {"trades": len(sub), "total_r": float(sum(t["r"] for t in sub))}
        for d in ("ACHAT", "VENTE")
        for sub in [[t for t in trades if t["direction"] == d]]}
    out["by_regime"] = {
        g: {"trades": len(sub), "avg_r": float(np.mean([t["r"] for t in sub])) if sub else 0.0}
        for g in ("tendance", "range")
        for sub in [[t for t in trades if t["regime"] == g]]}
    return out


def verdict(summary: dict) -> str:
    """Lecture prudente du résultat (jamais une promesse)."""
    n = summary["trades"]
    if n < 30:
        return (f"Échantillon trop petit ({n} trades) pour conclure quoi que ce soit : "
                "il faut au moins ~30 trades, idéalement 100+.")
    ci = summary.get("win_rate_ci")
    be = summary.get("breakeven_win_rate")
    if summary["avg_r"] > 0.05 and ci and be and ci[0] > be:
        return ("Espérance positive ET taux de réussite statistiquement au-dessus du seuil "
                "de rentabilité : signe encourageant (pas une garantie).")
    if summary["avg_r"] > 0:
        return ("Espérance légèrement positive mais NON démontrée statistiquement "
                "(l'intervalle de confiance couvre le seuil de rentabilité).")
    return ("Espérance nulle ou négative : sur cette période, le bot n'a pas "
            "d'avantage sur cet actif / ce timeframe. Ne le trade pas tel quel.")


def format_report(asset: str, timeframe: str, summary: dict, source: str = "") -> str:
    s = summary
    pf = ("∞" if s["profit_factor"] == float("inf")
          else (f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "n/a"))
    wr = "n/a"
    if s["win_rate"] is not None:
        lo, hi = s["win_rate_ci"]
        wr = f"{s['win_rate']:.0%} (IC95 {lo:.0%}-{hi:.0%})"
    be = f"{s['breakeven_win_rate']:.0%}" if s["breakeven_win_rate"] else "n/a"
    lines = [
        f"BACKTEST {asset} {timeframe} — {s['bars']} bougies" + (f" — {source}" if source else ""),
        f"Trades : {s['trades']} ({s['wins']} TP, {s['losses']} SL, {s['expired']} expirés)",
        f"Taux de réussite TP vs SL : {wr} — seuil de rentabilité : {be}",
        f"Espérance : {s['avg_r']:+.2f} R par trade — total {s['total_r']:+.1f} R",
        f"Facteur de profit : {pf} — drawdown max : {s['max_drawdown_r']:.1f} R — "
        f"pire série perdante : {s['max_losing_streak']}",
        f"Durée moyenne d'un trade : {s['avg_bars']:.0f} bougies",
        f"Coût aller-retour supposé : {s['cost_pct']:g}% du prix",
    ]
    if s["buy_hold_pct"] is not None:
        lines.append(f"Pour comparer, acheter et garder sur la période : {s['buy_hold_pct']:+.1f}%")
    d = s["by_direction"]
    lines.append(f"Achats : {d['ACHAT']['trades']} trades ({d['ACHAT']['total_r']:+.1f} R) — "
                 f"Ventes : {d['VENTE']['trades']} trades ({d['VENTE']['total_r']:+.1f} R)")
    g = s["by_regime"]
    lines.append(f"En tendance : {g['tendance']['trades']} trades ({g['tendance']['avg_r']:+.2f} R/trade) — "
                 f"en range : {g['range']['trades']} trades ({g['range']['avg_r']:+.2f} R/trade)")
    lines.append("")
    lines.append(verdict(s))
    return "\n".join(lines)
