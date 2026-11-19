"""Tests du moteur de signal et de la gestion du risque (sans réseau).

Lancement : python tests/test_signal_engine.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core import risk  # noqa: E402
from core.signal_engine import analyze, DIRECTIONS  # noqa: E402


def _df(prices, wick=0.004):
    n = len(prices)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
        "open": prices,
        "high": [p * (1 + wick) for p in prices],
        "low": [p * (1 - wick) for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
    })


def test_direction_valide_et_coherence_niveaux():
    import random
    random.seed(3)
    # tendance montante avec bruit -> plutôt ACHAT
    prices = [100 * (1 + 0.004 * i) + random.uniform(-0.3, 0.3) for i in range(120)]
    sig = analyze(_df(prices))
    assert sig["direction"] in DIRECTIONS
    assert 0 <= sig["confidence"] <= 100
    e, tp, sl = sig["entry"], sig["tp"], sig["sl"]
    if sig["direction"] == "ACHAT":
        assert tp > e > sl, f"ACHAT : TP {tp} > entrée {e} > SL {sl} requis"
    elif sig["direction"] == "VENTE":
        assert tp < e < sl, f"VENTE : TP {tp} < entrée {e} < SL {sl} requis"
    else:
        # en ATTENDRE, les niveaux suivent quand même le biais dominant
        if sig["buy_points"] >= sig["sell_points"]:
            assert tp > e > sl
        else:
            assert tp < e < sl


def test_tendance_baissiere_penche_vente():
    import random
    random.seed(11)
    prices = [200 * (1 - 0.003 * i) + random.uniform(-0.4, 0.4) for i in range(120)]
    sig = analyze(_df(prices))
    assert sig["sell_points"] > sig["buy_points"], \
        "une forte baisse doit donner plus de points vendeurs"


def test_points_dans_limites():
    import random
    random.seed(5)
    prices = [100 + random.uniform(-1, 1) for _ in range(150)]
    sig = analyze(_df(prices))
    assert 0 <= sig["buy_points"] <= 12
    assert 0 <= sig["sell_points"] <= 12
    assert sig["rr"] >= 0


def test_risk_levels_haussiers():
    lv = risk.build_levels(100.0, 2.0, +1)
    assert lv["tp"] > lv["entry"] > lv["sl"]
    assert lv["rr"] > 0
    assert abs(lv["tp"] - 103.0) < 1e-9      # 100 + 1.5*2
    assert abs(lv["sl"] - 98.0) < 1e-9       # 100 - 1.0*2


def test_risk_levels_baissiers():
    lv = risk.build_levels(100.0, 2.0, -1)
    assert lv["tp"] < lv["entry"] < lv["sl"]
    assert abs(lv["tp"] - 97.0) < 1e-9
    assert abs(lv["sl"] - 102.0) < 1e-9


def test_position_hint_sans_balance():
    from config import settings
    old = settings.BALANCE
    settings.BALANCE = 0
    assert risk.position_hint(100, 98) is None
    settings.BALANCE = 1000
    settings.RISK_PCT = 1.0
    hint = risk.position_hint(100, 98)  # risque 2 par unité -> 10/2 = 5 unités
    assert hint and "5" in hint
    settings.BALANCE = old


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n{len(tests)} tests moteur/risque : tous passent")
