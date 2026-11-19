"""Tests du package core : indicateurs techniques purs (sans réseau).

Lancement : python tests/test_indicators.py  (ou pytest tests/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core import indicators  # noqa: E402


def _fake_df(prices):
    """Construit un DataFrame OHLC minimal à partir d'une liste de clôtures."""
    n = len(prices)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
        "open": prices,
        "high": [p * 1.001 for p in prices],
        "low": [p * 0.999 for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
    })


def test_ema_order_on_uptrend():
    prices = [100 + i * 0.5 for i in range(60)]
    df = _fake_df(prices)
    s = indicators.compute_series(df)
    assert float(s["ema9"].iloc[-1]) > float(s["ema21"].iloc[-1]), \
        "en hausse, l'EMA9 doit être au-dessus de l'EMA21"


def test_rsi_extremes():
    up = [100 + i for i in range(60)]
    rsi_up = float(indicators.rsi(pd.Series(up)).iloc[-1])
    assert rsi_up > 90, f"RSI d'une série 100% haussière = {rsi_up}, attendu > 90"

    down = [200 - i for i in range(60)]
    rsi_down = float(indicators.rsi(pd.Series(down)).iloc[-1])
    assert rsi_down < 10, f"RSI d'une série 100% baissière = {rsi_down}, attendu < 10"


def test_rsi_in_range():
    import random
    random.seed(42)
    prices = [100 + random.uniform(-2, 2) for _ in range(200)]
    rsi_s = indicators.rsi(pd.Series(prices)).dropna()
    assert ((rsi_s >= 0) & (rsi_s <= 100)).all(), "RSI doit rester dans [0, 100]"


def test_atr_positive():
    df = _fake_df([100 + (i % 5) for i in range(60)])
    atr_s = indicators.atr(df).dropna()
    assert (atr_s > 0).all(), "ATR doit être strictement positif"


def test_bollinger_brackets_price():
    import random
    random.seed(7)
    prices = [100 + random.uniform(-1, 1) for _ in range(100)]
    close = pd.Series(prices)
    mid, up, low = indicators.bollinger(close, 20, 2.0)
    inside = ((close.iloc[20:] <= up.iloc[20:]) & (close.iloc[20:] >= low.iloc[20:])).mean()
    assert inside > 0.85, "les prix doivent rester (presque) tous dans les bandes"
    assert (up.iloc[-1] > mid.iloc[-1] > low.iloc[-1]), "bande haute > milieu > bande basse"


def test_stochastic_range():
    df = _fake_df([100 + (i % 7) * 0.5 for i in range(60)])
    k, d = indicators.stochastic(df)
    k = k.dropna()
    assert ((k >= 0) & (k <= 100)).all(), "%K doit rester dans [0, 100]"


def test_smart_round():
    assert indicators.smart_round(63666.12345) == 63666.12   # >= 100 : 2 décimales
    assert indicators.smart_round(1.0969123) == 1.0969       # >= 1 : 4 décimales
    assert indicators.smart_round(159.55123) == 159.55       # >= 100 : 2 décimales
    assert indicators.smart_round(0.551234) == 0.55123       # >= 0.1 : 5 décimales
    assert indicators.smart_round(0.001234) == 0.001234      # < 0.1 : 6 décimales


def test_macd_shape():
    prices = pd.Series([100 + (i % 6) for i in range(80)])
    line, sig, hist = indicators.macd(prices)
    assert len(line) == len(sig) == len(hist) == 80
    assert abs((line.iloc[-1] - sig.iloc[-1]) - hist.iloc[-1]) < 1e-9, \
        "histogramme = ligne - signal"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n{len(tests)} tests indicateurs : tous passent")
