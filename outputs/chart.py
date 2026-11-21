"""
Génération de l'image d'analyse envoyée par le bot.

L'image contient, sur le même graphique :
- les bougies japonaises FERMÉES (80 dernières par défaut)
- les moyennes mobiles EMA 9 (orange), EMA 21 (bleu) et EMA 50 (violet)
- les bandes de Bollinger (zone grisée)
- les lignes Entrée / Take Profit / Stop Loss — UNIQUEMENT si un signal
  ACHAT/VENTE est confirmé (sur ATTENDRE on ne trace aucun niveau : l'image
  de la v1 montrait un TP/SL alors que le bot disait de ne pas entrer)
- un marqueur sur la dernière bougie fermée
- en dessous, les panneaux RSI(14), MACD(12,26,9) et ADX(14) : exactement les
  indicateurs utilisés pour décider, y compris le régime tendance / range.
"""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from config import settings
from core import indicators

UP = "#26a69a"      # bougie haussière
DOWN = "#ef5350"    # bougie baissière
GRID = "#2b2b2b"
DARK = "#12141a"
FG = "#e8e8e8"
SUB = "#9aa0a6"

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _label_box():
    return dict(boxstyle="round,pad=0.2", facecolor=DARK, edgecolor="none", alpha=0.85)


def _fmt_price(v: float, decimals: int | None) -> str:
    return f"{indicators.smart_round(v, decimals):g}" if decimals is None else f"{v:.{decimals}f}"


def make_chart(df: pd.DataFrame, signal: dict, asset: str, timeframe: str,
               source_label: str, label: str = "", decimals: int | None = None) -> bytes:
    n = min(settings.NB_CANDLES_CHART, len(df))
    d = df.tail(n).reset_index(drop=True)
    ind = signal["indicators"]
    direction = signal["direction"]

    fig = plt.figure(figsize=(12.5, 10.5), facecolor=DARK, constrained_layout=True)
    gs = fig.add_gridspec(4, 1, height_ratios=[3.2, 1.0, 1.0, 0.9])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax4 = fig.add_subplot(gs[3], sharex=ax1)
    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor(DARK)
        ax.tick_params(colors=FG, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#3a3a3a")
        ax.grid(True, color=GRID, linewidth=0.5, alpha=0.6)
    for ax in (ax1, ax2, ax3):
        ax.tick_params(labelbottom=False)

    # ------------------------------------------------ bougies
    x = list(range(len(d)))
    colors = [UP if c >= o else DOWN for o, c in zip(d["open"], d["close"])]
    ax1.vlines(x, d["low"], d["high"], colors=colors, linewidth=1.1, zorder=2)
    body_low = d[["open", "close"]].min(axis=1)
    body_h = (d["close"] - d["open"]).abs().clip(lower=1e-12)
    ax1.bar(x, body_h, bottom=body_low, width=0.64, color=colors, edgecolor=colors,
            zorder=3)

    # Indicateurs calculés sur la SÉRIE COMPLÈTE puis découpés sur la fenêtre :
    # évite le « warm-up » qui déformait le début des courbes.
    s = indicators.compute_series(df)

    def tail(name):
        return s[name].tail(n).reset_index(drop=True)

    ax1.plot(x, tail("ema9"), color="#f4a261", linewidth=1.4, label="EMA 9", zorder=4)
    ax1.plot(x, tail("ema21"), color="#4d96ff", linewidth=1.4, label="EMA 21", zorder=4)
    ax1.plot(x, tail("ema50"), color="#b388ff", linewidth=1.2, label="EMA 50", zorder=4)
    ax1.plot(x, tail("bb_upper"), color="#888888", linewidth=0.8, linestyle=":", zorder=1)
    ax1.plot(x, tail("bb_lower"), color="#888888", linewidth=0.8, linestyle=":", zorder=1)
    ax1.fill_between(x, tail("bb_lower"), tail("bb_upper"), color="#4d96ff", alpha=0.05, zorder=0)

    # ------------------------------------------------ niveaux (seulement si signal)
    x_right = n + 1.5
    ax1.set_xlim(-1, n + 8)
    last_close = float(d["close"].iloc[-1])
    if direction != "ATTENDRE":
        for key, color, name, lw, bold in (("entry", "#cccccc", "Entrée", 1.1, "normal"),
                                           ("tp", "#4caf50", "TP", 1.3, "bold"),
                                           ("sl", "#e53935", "SL", 1.3, "bold")):
            ax1.axhline(signal[key], color=color, linestyle="--", linewidth=lw, zorder=1)
            ax1.text(x_right, signal[key], f"{name} {_fmt_price(signal[key], decimals)}",
                     color=color, fontsize=9, va="center", ha="left",
                     fontweight=bold, bbox=_label_box(), zorder=7)
    else:
        ax1.axhline(last_close, color="#777777", linestyle=":", linewidth=1.0, zorder=1)
        ax1.text(x_right, last_close, f"Dernier {_fmt_price(last_close, decimals)}",
                 color="#aaaaaa", fontsize=9, va="center", ha="left", bbox=_label_box())

    # ------------------------------------------------ marqueur du signal
    marker = {"ACHAT": "^", "VENTE": "v", "ATTENDRE": "o"}[direction]
    mcolor = {"ACHAT": "#4caf50", "VENTE": "#e53935", "ATTENDRE": "#f4c20d"}[direction]
    ax1.scatter([n - 1], [last_close], marker=marker, s=200, color=mcolor, zorder=6)
    ax1.legend(loc="upper left", fontsize=9, facecolor="#1a1c24", edgecolor="#3a3a3a",
               labelcolor=FG, framealpha=0.9, ncols=3)

    # ------------------------------------------------ titre
    name = f"{asset}" + (f" · {label}" if label and label != asset else "")
    regime = signal.get("regime", "")
    adx_v = ind.get("adx")
    regime_txt = (f"Régime : {regime.upper()} (ADX {adx_v:.0f})"
                  if regime and adx_v == adx_v else "")
    sub1 = (f"Signal : {direction}  |  Score {signal.get('score', 0)}/{signal.get('max_points', 14)}  |  "
            f"Accord {signal['confidence']}%  |  ACHAT {ind['buy_points']} pts vs VENTE {ind['sell_points']} pts"
            + (f"  |  {regime_txt}" if regime_txt else ""))
    if direction == "ATTENDRE" and signal.get("blocked_by"):
        sub2 = f"Signal {signal.get('raw_direction', '')} annulé par un filtre de qualité"
    elif direction == "ATTENDRE":
        sub2 = "Pas de position conseillée, on observe"
    else:
        sub2 = f"Source des cours : {source_label}"
    if signal.get("stale"):
        sub2 += "  |  MARCHÉ FERMÉ ou données anciennes : niveaux basés sur la dernière clôture"
    ax1.set_title(f"{name}  —  {timeframe}    (bougies fermées uniquement)",
                  color=FG, fontsize=12, fontweight="bold", loc="left", pad=36)
    ax1.text(0, 1.045, sub1, transform=ax1.transAxes, color=SUB, fontsize=9)
    ax1.text(0, 1.012, sub2, transform=ax1.transAxes, color=SUB, fontsize=9)

    # ------------------------------------------------ RSI
    ax2.plot(x, tail("rsi"), color="#b388ff", linewidth=1.3)
    ax2.axhline(70, color="#e53935", linestyle="--", linewidth=0.8)
    ax2.axhline(30, color="#4caf50", linestyle="--", linewidth=0.8)
    ax2.fill_between(x, 30, 70, color="#4d96ff", alpha=0.06)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI 14", color=FG, fontsize=9)
    ax2.text(n + 1.5, 70, "70", color="#e53935", fontsize=8, va="center")
    ax2.text(n + 1.5, 30, "30", color="#4caf50", fontsize=8, va="center")

    # ------------------------------------------------ MACD
    hist = tail("macd_hist")
    ax3.plot(x, tail("macd_line"), color="#4d96ff", linewidth=1.2, label="MACD")
    ax3.plot(x, tail("macd_signal"), color="#f4a261", linewidth=1.2, label="Signal")
    ax3.bar(x, hist, color=[UP if v >= 0 else DOWN for v in hist], alpha=0.55, width=0.64)
    ax3.axhline(0, color="#3a3a3a", linewidth=0.8)
    ax3.set_ylabel("MACD", color=FG, fontsize=9)
    ax3.legend(loc="upper left", fontsize=8, facecolor="#1a1c24", edgecolor="#3a3a3a",
               labelcolor=FG, framealpha=0.9, ncols=2)

    # ------------------------------------------------ ADX (régime)
    ax4.plot(x, tail("adx"), color="#f4c20d", linewidth=1.3, label="ADX")
    ax4.plot(x, tail("plus_di"), color=UP, linewidth=0.9, label="DI+")
    ax4.plot(x, tail("minus_di"), color=DOWN, linewidth=0.9, label="DI-")
    ax4.axhline(settings.ADX_TREND, color="#cccccc", linestyle="--", linewidth=0.8)
    ax4.text(n + 1.5, settings.ADX_TREND, f"{settings.ADX_TREND:g} tendance", color="#cccccc",
             fontsize=8, va="center")
    ax4.set_ylim(0, max(50, float(pd.concat([tail("adx"), tail("plus_di"),
                                             tail("minus_di")]).max(skipna=True) or 50) + 5))
    ax4.set_ylabel("ADX 14", color=FG, fontsize=9)
    ax4.legend(loc="upper left", fontsize=8, facecolor="#1a1c24", edgecolor="#3a3a3a",
               labelcolor=FG, framealpha=0.9, ncols=3)

    # ------------------------------------------------ axe du temps
    ticks = list(range(0, n, max(n // 8, 1)))
    fmt = "%d/%m/%y" if timeframe in ("1d", "1w") else "%d/%m %H:%M"
    ax4.set_xticks(ticks)
    ax4.set_xticklabels([d["time"].iloc[i].strftime(fmt) for i in ticks], fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=DARK)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
