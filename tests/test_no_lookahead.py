"""Aucune fuite du futur : c'est la condition pour que le backtest soit honnête
et que le signal ne « repeigne » pas après coup."""
import numpy as np
import pandas as pd

from core import indicators, signal_engine as se
from synthetic import make_ohlc


def test_indicateurs_causaux():
    """La valeur d'un indicateur à la bougie k ne dépend pas des bougies > k."""
    df = make_ohlc(400, seed=7, drift=0.0003, vol=0.003)
    full = indicators.compute_series(df)
    k = 250
    part = indicators.compute_series(df.iloc[: k + 1])
    for name in full:
        a, b = full[name].iloc[k], part[name].iloc[k]
        if pd.isna(a) and pd.isna(b):
            continue
        assert np.isclose(a, b, rtol=1e-9, atol=1e-9), f"{name} dépend du futur : {a} vs {b}"


def test_signal_independant_des_bougies_futures():
    """analyze_at(k) ne change pas si on bouleverse toutes les bougies après k."""
    df = make_ohlc(500, seed=11, drift=0.0, vol=0.003)
    k = 300
    s1 = indicators.compute_series(df)
    a1 = se.analyze_at(df, s1, k)

    df2 = df.copy()
    rng = np.random.default_rng(0)
    for col in ("open", "high", "low", "close"):
        df2.loc[k + 1:, col] = df2.loc[k + 1:, col] * (1 + rng.normal(0, 0.2, len(df2) - k - 1))
    df2["high"] = df2[["high", "open", "close"]].max(axis=1)
    df2["low"] = df2[["low", "open", "close"]].min(axis=1)
    s2 = indicators.compute_series(df2)
    a2 = se.analyze_at(df2, s2, k)

    for key in ("direction", "buy_points", "sell_points", "entry", "tp", "sl", "regime", "confidence"):
        assert a1[key] == a2[key], f"{key} dépend du futur"


def test_analyze_equivaut_a_analyze_at_derniere_bougie():
    df = make_ohlc(300, seed=3, drift=0.0005, vol=0.003)
    a = se.analyze(df)
    b = se.analyze_at(df, indicators.compute_series(df), len(df) - 1)
    assert a["direction"] == b["direction"] and a["buy_points"] == b["buy_points"]
