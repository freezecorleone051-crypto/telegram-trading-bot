"""Tests du détecteur de chandeliers japonais (sans réseau).

Lancement : python tests/test_patterns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core import patterns  # noqa: E402


def _df(rows):
    """rows = [(open, high, low, close), ...]"""
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=len(rows), freq="5min", tz="UTC"),
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "volume": [1000.0] * len(rows),
    })


def _names(found):
    return [f["name"] for f in found]


def test_pin_bar_haussiere():
    # mèche basse longue : corps 100->101, bas 96 (mèche 5), haut 101.5
    df = _df([(100.5, 101, 100, 100.5),
              (100.5, 101, 99.5, 101),
              (101, 102, 100.5, 101.5),
              (101.5, 101.5, 96.0, 101.0)])
    found = patterns.detect_patterns(df)
    assert "Pin bar haussière (marteau)" in _names(found), _names(found)


def test_pin_bar_baissiere():
    # mèche haute longue : corps 105->104, haut 110, bas 103.5
    df = _df([(104.5, 105, 104, 104.5),
              (104.5, 105.5, 104, 105),
              (105, 105.5, 104.5, 104),
              (104, 110.0, 103.5, 104.5)])
    found = patterns.detect_patterns(df)
    assert "Pin bar baissière (étoile filante)" in _names(found), _names(found)


def test_englobante_haussiere():
    # bougie rouge petite puis bougie verte qui l'avale
    df = _df([(100, 101, 99.5, 100.5),
              (101, 101.5, 100.5, 101),
              (101.2, 101.8, 100.8, 101.0),   # petite rouge (open 101.2, close 101.0)
              (101.0, 102.5, 100.6, 102.0)])  # verte qui avale (open 101.0, close 102.0)
    found = patterns.detect_patterns(df)
    assert "Englobante haussière" in _names(found), _names(found)


def test_englobante_baissiere():
    df = _df([(100, 101, 99.5, 100.5),
              (101, 101.5, 100.5, 101),
              (101, 101.4, 100.6, 101.2),   # petite verte
              (101.4, 101.6, 100.0, 100.4)])  # rouge qui avale
    found = patterns.detect_patterns(df)
    assert "Englobante baissière" in _names(found), _names(found)


def test_doji():
    # corps minuscule au milieu d'une grande plage
    df = _df([(100, 101, 99, 100.5),
              (100.5, 101.5, 100, 101),
              (101, 102, 100.5, 101.5),
              (101.5, 103.5, 99.5, 101.55)])  # corps 0.05 sur plage 4.0
    found = patterns.detect_patterns(df)
    assert "Doji" in _names(found), _names(found)


def test_trois_soldats_blancs():
    df = _df([(100, 100.6, 99.8, 100.4),
              (100.4, 101.2, 100.2, 101.0),
              (101.0, 101.8, 100.8, 101.6),
              (101.6, 102.6, 101.4, 102.4)])
    found = patterns.detect_patterns(df)
    assert "Trois soldats blancs" in _names(found), _names(found)


def test_trois_corbeaux_noirs():
    df = _df([(100, 100.6, 99.8, 100.4),
              (100.4, 101.0, 100.0, 100.2),
              (100.2, 100.3, 99.4, 99.6),
              (99.6, 99.8, 98.6, 98.8)])
    found = patterns.detect_patterns(df)
    assert "Trois corbeaux noirs" in _names(found), _names(found)


def test_sides_valides():
    df = _df([(100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.3 + i * 0.1)
              for i in range(6)])
    for f in patterns.detect_patterns(df):
        assert f["side"] in ("buy", "sell", "neutral")
        assert f["points"] >= 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n{len(tests)} tests chandeliers : tous passent")
