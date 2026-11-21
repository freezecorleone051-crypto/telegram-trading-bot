"""Moteur de backtest : simulation de trade, statistiques, honnêteté."""
import numpy as np
import pandas as pd
import pytest

from core import indicators
from core.risk import build_levels
from services import backtest as bt
from synthetic import make_ohlc


def _arr(*rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2]       # highs, lows, closes


def test_simulate_win_loss_both():
    h, l, c = _arr((101, 99.5, 100.5), (103.5, 100, 103))
    st, j, r = bt.simulate_trade(h, l, c, 0, "ACHAT", 100, 103, 98, 10)
    assert st == "win" and j == 1 and r == pytest.approx(1.5)
    h, l, c = _arr((101, 99.5, 100.5), (100.5, 97.5, 98))
    assert bt.simulate_trade(h, l, c, 0, "ACHAT", 100, 103, 98, 10)[0] == "loss"
    h, l, c = _arr((101, 99.5, 100.5), (104, 97, 100))
    assert bt.simulate_trade(h, l, c, 0, "ACHAT", 100, 103, 98, 10)[0] == "loss"   # SL prioritaire


def test_simulate_vente():
    h, l, c = _arr((100.5, 99.5, 100), (100.2, 96.5, 97))
    assert bt.simulate_trade(h, l, c, 0, "VENTE", 100, 97, 102, 10)[0] == "win"


def test_simulate_expire_et_open():
    h, l, c = _arr(*[(100.5, 99.5, 100.5)] * 6)
    st, j, r = bt.simulate_trade(h, l, c, 0, "ACHAT", 100, 103, 98, 3)
    assert st == "expired" and j == 3 and r == pytest.approx(0.25)
    st, j, r = bt.simulate_trade(h, l, c, 3, "ACHAT", 100, 103, 98, 10)   # pas assez de données
    assert st == "open"


def test_statistiques():
    trades = ([{"status": "win", "r": 1.5, "tp": 103, "entry": 100, "sl": 98, "bars": 3,
                "direction": "ACHAT", "regime": "tendance"}] * 4
              + [{"status": "loss", "r": -1.0, "tp": 103, "entry": 100, "sl": 98, "bars": 2,
                  "direction": "VENTE", "regime": "range"}] * 6)
    s = bt.summarize(trades, n_bars=500, buy_hold_pct=5.0)
    assert s["trades"] == 10 and s["wins"] == 4
    assert s["win_rate"] == pytest.approx(0.4)
    assert s["total_r"] == pytest.approx(0.0)
    assert s["profit_factor"] == pytest.approx(1.0)
    assert s["breakeven_win_rate"] == pytest.approx(0.4)
    assert s["max_losing_streak"] == 6
    assert s["max_drawdown_r"] == pytest.approx(6.0)
    assert "trop petit" in bt.verdict(s)          # 10 trades : pas de conclusion


def test_les_couts_sont_retires():
    df = make_ohlc(1500, seed=5, drift=0.0, vol=0.002)
    free = bt.run_backtest(df, "15m")["summary"]
    paid = bt.run_backtest(df, "15m", cost_pct=0.2)["summary"]
    assert paid["trades"] == free["trades"]
    assert paid["total_r"] < free["total_r"]


def test_pas_d_avantage_invente_sur_marche_aleatoire():
    """Garde-fou central : sur du hasard pur, l'espérance reste proche de celle
    d'entrées aléatoires (ici ~0), pas d'un faux « bon résultat »."""
    total = n = 0
    for sd in (1, 2, 3, 4):
        df = make_ohlc(2500, seed=sd, drift=0.0, vol=0.002)
        s = bt.run_backtest(df, "15m")["summary"]
        total += s["total_r"]
        n += s["trades"]
    assert n > 100
    assert abs(total / n) < 0.20, f"espérance suspecte sur du hasard pur : {total / n:+.3f} R"


def test_pas_de_fuite_du_futur_dans_le_backtest():
    """Les trades trouvés sur les 1500 premières bougies sont identiques que le
    jeu de données soit tronqué ou prolongé."""
    df = make_ohlc(2500, seed=9, drift=0.0003, vol=0.002)
    a = bt.run_backtest(df.iloc[:1500], "15m")["trades"]
    b = bt.run_backtest(df, "15m")["trades"]
    common = [t for t in b if t["bar_time"] <= str(df["time"].iloc[1400])]
    mine = [t for t in a if t["bar_time"] <= str(df["time"].iloc[1400])]
    assert [(t["bar_time"], t["direction"]) for t in mine] == [(t["bar_time"], t["direction"]) for t in common]


def test_marche_en_range_gagne_avec_retour_a_la_moyenne():
    """Simulation mean-reverting (Ornstein-Uhlenbeck) : la logique « range » du
    moteur doit y battre un hasard à espérance négative. (Test de COMPORTEMENT
    sur données simulées, pas une promesse de gain sur le vrai marché.)"""
    total = n = 0
    for sd in range(21, 27):
        df = make_ohlc(segments=[(600, 0.0, 0.0025, 0.06)] * 5, seed=sd)
        s = bt.run_backtest(df, "15m")["summary"]
        total += s["total_r"]
        n += s["trades"]
    assert n > 300
    assert total / n > 0.0, f"{total / n:+.3f}"


def test_filtre_htf_ne_plante_pas_et_reduit_les_trades():
    df = make_ohlc(segments=[(300, 0.0012, 0.002), (300, -0.0012, 0.002)] * 3, seed=4)
    base = bt.run_backtest(df, "15m")["summary"]["trades"]
    filt = bt.run_backtest(df, "15m", use_mtf=True)["summary"]["trades"]
    assert 0 < filt <= base
