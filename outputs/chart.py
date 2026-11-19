"""
Génération de l'image d'analyse envoyée par le bot (la "photo" demandée).

L'image contient, sur le même graphique :
- les bougies japonaises (80 dernières bougies)
- les moyennes mobiles EMA 9 (orange) et EMA 21 (bleu)
- les bandes de Bollinger (zone grisée)
- les lignes Entrée (gris), Take Profit (vert) et Stop Loss (rouge)
- un marqueur du signal sur la dernière bougie
- en dessous, les panneaux RSI(14) et MACD(12,26,9) : exactement les
  indicateurs utilisés pour décider -> on voit COMMENT le bot a raisonné.
"""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from config import settings
from core import indicators

UP = "#26a69a"      # bougie haussière
DOWN = "#ef5350"    # bougie baissière
GRID = "#2b2b2b"

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

DARK = "#12141a"
FG = "#e8e8e8"


def make_chart(df: pd.DataFrame, signal: dict, asset: str, timeframe: str,
               source_label: str) -> bytes:
    n = min(settings.NB_CANDLES_CHART, len(df))
    d = df.tail(n).reset_index(drop=True)
    ind = signal["indicators"]

    fig = plt.figure(figsize=(12.5, 9), facecolor=DARK, constrained_layout=True)
    gs = fig.add_gridspec(3, 1, height_ratios=[3.2, 1.0, 1.0])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    for ax in (ax1, ax2, ax3):
        ax.set_facecolor(DARK)
        ax.tick_params(colors=FG, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#3a3a3a")
        ax.grid(True, color=GRID, linewidth=0.5, alpha=0.6)
    # les heures seulement sur le panneau du bas
    ax1.tick_params(labelbottom=False)
    ax2.tick_params(labelbottom=False)

    # ------------------------------------------------ bougies
    for i, row in d.iterrows():
        color = UP if row["close"] >= row["open"] else DOWN
        ax1.plot([i, i], [row["low"], row["high"]], color=color, linewidth=1.1, zorder=2)
        body_low = min(row["open"], row["close"])
        body_h = max(abs(row["close"] - row["open"]), 1e-12)
        ax1.add_patch(Rectangle((i - 0.32, body_low), 0.64, body_h,
                                facecolor=color, edgecolor=color, zorder=3))

    x = range(len(d))
    # Indicateurs calculés sur la SÉRIE COMPLÈTE (250 bougies) puis découpés
    # sur la fenêtre : évite le "warm-up" qui déformait le début des courbes.
    s = indicators.compute_series(df)
    e9 = s["ema9"].tail(n).reset_index(drop=True)
    e21 = s["ema21"].tail(n).reset_index(drop=True)
    rsi_s = s["rsi"].tail(n).reset_index(drop=True)
    macd_line = s["macd_line"].tail(n).reset_index(drop=True)
    macd_sig = s["macd_signal"].tail(n).reset_index(drop=True)
    hist = s["macd_hist"].tail(n).reset_index(drop=True)
    bb_up = s["bb_upper"].tail(n).reset_index(drop=True)
    bb_low = s["bb_lower"].tail(n).reset_index(drop=True)

    ax1.plot(x, e9, color="#f4a261", linewidth=1.4, label="EMA 9", zorder=4)
    ax1.plot(x, e21, color="#4d96ff", linewidth=1.4, label="EMA 21", zorder=4)
    ax1.plot(x, bb_up, color="#888888", linewidth=0.8, linestyle=":", zorder=1)
    ax1.plot(x, bb_low, color="#888888", linewidth=0.8, linestyle=":", zorder=1)
    ax1.fill_between(x, bb_low, bb_up, color="#4d96ff", alpha=0.05, zorder=0)

    # ------------------------------------------------ lignes entrée / TP / SL
    ax1.axhline(signal["entry"], color="#cccccc", linestyle="--", linewidth=1.1, zorder=1)
    ax1.axhline(signal["tp"], color="#4caf50", linestyle="--", linewidth=1.3, zorder=1)
    ax1.axhline(signal["sl"], color="#e53935", linestyle="--", linewidth=1.3, zorder=1)
    x_right = n + 1.5
    ax1.set_xlim(-1, n + 6)
    ax1.text(x_right, signal["entry"], f"Entrée {indicators.smart_round(signal['entry'])}",
             color="#cccccc", fontsize=9, va="center", ha="left")
    ax1.text(x_right, signal["tp"], f"TP {indicators.smart_round(signal['tp'])}",
             color="#4caf50", fontsize=9, va="center", ha="left", fontweight="bold")
    ax1.text(x_right, signal["sl"], f"SL {indicators.smart_round(signal['sl'])}",
             color="#e53935", fontsize=9, va="center", ha="left", fontweight="bold")

    # ------------------------------------------------ marqueur du signal
    arrow = "^" if signal["direction"] == "ACHAT" else ("v" if signal["direction"] == "VENTE" else "o")
    mcolor = {"ACHAT": "#4caf50", "VENTE": "#e53935", "ATTENDRE": "#f4c20d"}[signal["direction"]]
    ax1.scatter([n - 1], [d["close"].iloc[-1]], marker=arrow, s=200, color=mcolor, zorder=6)

    ax1.legend(loc="upper left", fontsize=9, facecolor="#1a1c24", edgecolor="#3a3a3a",
               labelcolor=FG, framealpha=0.9)

    sub = (f"Signal : {signal['direction']}  |  Confiance : {signal['confidence']}%  |  "
           f"Points ACHAT {ind['buy_points']} vs VENTE {ind['sell_points']}  |  "
           f"Source des cours : {source_label}")
    if signal["direction"] == "ATTENDRE":
        sub += "  |  Pas de position conseillée, on observe"
    ax1.set_title(f"{asset}  —  {timeframe}    (EMA 9/21 + Bollinger + RSI + MACD + Stochastique + ATR)",
                  color=FG, fontsize=12, fontweight="bold", loc="left", pad=24)
    ax1.text(0, 1.012, sub, transform=ax1.transAxes, color="#9aa0a6", fontsize=9)

    # ------------------------------------------------ RSI
    ax2.plot(x, rsi_s, color="#b388ff", linewidth=1.3)
    ax2.axhline(70, color="#e53935", linestyle="--", linewidth=0.8)
    ax2.axhline(30, color="#4caf50", linestyle="--", linewidth=0.8)
    ax2.fill_between(x, 30, 70, color="#4d96ff", alpha=0.06)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI 14", color=FG, fontsize=9)
    ax2.text(n + 1.5, 70, "70 surachat", color="#e53935", fontsize=8, va="center")
    ax2.text(n + 1.5, 30, "30 survente", color="#4caf50", fontsize=8, va="center")

    # ------------------------------------------------ MACD
    ax3.plot(x, macd_line, color="#4d96ff", linewidth=1.2, label="MACD")
    ax3.plot(x, macd_sig, color="#f4a261", linewidth=1.2, label="Signal")
    ax3.bar(x, hist, color=[UP if v >= 0 else DOWN for v in hist], alpha=0.55, width=0.64)
    ax3.axhline(0, color="#3a3a3a", linewidth=0.8)
    ax3.set_ylabel("MACD", color=FG, fontsize=9)
    ax3.legend(loc="upper left", fontsize=8, facecolor="#1a1c24", edgecolor="#3a3a3a",
               labelcolor=FG, framealpha=0.9, ncols=2)

    # ------------------------------------------------ axe du temps
    ticks = list(range(0, n, max(n // 8, 1)))
    labels = [d["time"].iloc[i].strftime("%d/%m %H:%M") for i in ticks]
    ax3.set_xticks(ticks)
    ax3.set_xticklabels(labels, rotation=0, fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=DARK)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
