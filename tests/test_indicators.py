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
    assert indicators.smart_round(1.0969123) == 1.09691      # 1-10 : 5 décimales (standard forex)
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


def test_rsi_marche_plat_vaut_50():
    # v1 : un marché parfaitement plat donnait RSI = 0 (« survente ») -> faux signal d'achat
    flat = pd.Series([100.0] * 60)
    assert float(indicators.rsi(flat).iloc[-1]) == 50.0


def test_rsi_hausse_pure_vaut_100_et_baisse_pure_vaut_0():
    up = pd.Series([100.0 + i for i in range(60)])
    down = pd.Series([200.0 - i for i in range(60)])
    assert float(indicators.rsi(up).iloc[-1]) == 100.0
    assert float(indicators.rsi(down).iloc[-1]) == 0.0


def test_stochastique_marche_plat_vaut_50():
    n = 40
    df = pd.DataFrame({"open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n,
                       "close": [10.0] * n, "volume": [1.0] * n})
    k, d = indicators.stochastic(df)
    assert float(k.iloc[-1]) == 50.0


def test_adx_tendance_forte_vs_range():
    from synthetic import make_ohlc
    trend = make_ohlc(300, seed=1, drift=0.002, vol=0.001)
    rng = make_ohlc(300, seed=2, drift=0.0, vol=0.002, reversion=0.15)
    adx_t = float(indicators.adx(trend)[0].iloc[-1])
    adx_r = float(indicators.adx(rng)[0].iloc[-1])
    assert adx_t > 30 > adx_r or adx_t > adx_r + 10, (adx_t, adx_r)
    pdi, mdi = indicators.adx(trend)[1].iloc[-1], indicators.adx(trend)[2].iloc[-1]
    assert pdi > mdi          # tendance haussière : DI+ > DI-


def test_volume_ratio_nan_sans_volume():
    n = 40
    df = pd.DataFrame({"open": [1.0] * n, "high": [1.1] * n, "low": [0.9] * n,
                       "close": [1.0] * n, "volume": [0.0] * n})
    assert pd.isna(indicators.volume_ratio(df).iloc[-1])
