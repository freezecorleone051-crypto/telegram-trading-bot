"""Comportement du moteur v2 : régimes, filtres, cohérence des niveaux."""
import numpy as np
import pandas as pd
import pytest

from config import settings
from core import indicators, risk, signal_engine as se
from synthetic import make_ohlc


def _first_signal(df, start=80, want=None):
    s = indicators.compute_series(df)
    for i in range(start, len(df)):
        a = se.analyze_at(df, s, i)
        if a["direction"] != "ATTENDRE" and (want is None or a["direction"] == want):
            return a
    return None


def test_trop_peu_de_bougies():
    with pytest.raises(se.InsufficientData):
        se.analyze(make_ohlc(30))


def test_tendance_haussiere_donne_achat_en_regime_tendance():
    df = make_ohlc(400, seed=3, drift=0.0006, vol=0.003)
    a = _first_signal(df, want="ACHAT")
    assert a is not None and a["regime"] == "tendance" and a["trend"] == "haussière"
    assert a["tp"] > a["entry"] > a["sl"]


def test_tendance_baissiere_donne_vente():
    df = make_ohlc(400, seed=6, drift=-0.0006, vol=0.003)
    a = _first_signal(df, want="VENTE")
    assert a is not None and a["tp"] < a["entry"] < a["sl"]


def test_marche_plat_sans_signal():
    n = 200
    df = pd.DataFrame({"time": pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC"),
                       "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0})
    a = se.analyze(df)
    assert a["direction"] == "ATTENDRE"


def test_range_ignore_ema_et_macd():
    df = make_ohlc(segments=[(600, 0.0, 0.0025, 0.06)] * 2, seed=22)
    s = indicators.compute_series(df)
    seen_range = False
    for i in range(100, len(df)):
        a = se.analyze_at(df, s, i)
        if a["regime"] == "range":
            seen_range = True
            assert a["max_points"] == se.MAX_POINTS_RANGE
            assert any("non compté en range" in r for r in a["reasons"])
            assert max(a["buy_points"], a["sell_points"]) <= se.MAX_POINTS_RANGE
    assert seen_range


def test_points_jamais_au_dessus_du_maximum():
    df = make_ohlc(1200, seed=8, drift=0.0, vol=0.004)
    s = indicators.compute_series(df)
    for i in range(80, len(df)):
        a = se.analyze_at(df, s, i)
        assert 0 <= a["buy_points"] <= se.MAX_POINTS and 0 <= a["sell_points"] <= se.MAX_POINTS
        assert 0 <= a["confidence"] <= 100
        assert a["direction"] in se.DIRECTIONS


def test_filtre_marche_trop_calme(monkeypatch):
    df = make_ohlc(400, seed=3, drift=0.0006, vol=0.003)
    assert _first_signal(df, want="ACHAT") is not None
    monkeypatch.setattr(settings, "MIN_ATR_PCT", 50.0)       # seuil absurde -> tout est « trop calme »
    s = indicators.compute_series(df)
    a = se.analyze_at(df, s, len(df) - 1)
    assert a["direction"] == "ATTENDRE"
    # au moins une bougie avait un signal brut annulé par le filtre
    blocked = [se.analyze_at(df, s, i) for i in range(80, len(df))]
    assert any(b["blocked_by"] and "trop calme" in b["blocked_by"] for b in blocked)


def test_filtre_etirement_desactive_par_defaut(monkeypatch):
    assert settings.MAX_STRETCH_ATR == 0.0
    df = make_ohlc(400, seed=3, drift=0.0006, vol=0.003)
    s = indicators.compute_series(df)
    n_off = sum(se.analyze_at(df, s, i)["direction"] != "ATTENDRE" for i in range(80, len(df)))
    monkeypatch.setattr(settings, "MAX_STRETCH_ATR", 0.5)
    n_on = sum(se.analyze_at(df, s, i)["direction"] != "ATTENDRE" for i in range(80, len(df)))
    assert n_on <= n_off


def test_niveaux_coherents_et_rr_exact():
    lv = risk.build_levels(1.09691, 0.00025, 1, decimals=5)
    assert lv["tp"] == pytest.approx(1.09691 + 1.5 * 0.00025, abs=1e-5)
    assert lv["sl"] == pytest.approx(1.09691 - 0.00025, abs=1e-5)
    assert lv["rr"] == pytest.approx(1.5)           # v1 : ratio faussé par l'arrondi


def test_niveaux_forex_jpy_3_decimales():
    lv = risk.build_levels(150.123, 0.05, -1, decimals=3)
    assert lv["tp"] < lv["entry"] < lv["sl"]
    assert round(lv["tp"], 3) == lv["tp"]


def test_taille_de_position(monkeypatch):
    monkeypatch.setattr(settings, "BALANCE", 1000.0)
    monkeypatch.setattr(settings, "RISK_PCT", 1.0)
    monkeypatch.setattr(settings, "LEVERAGE", 1.0)
    p = risk.position_size(100.0, 98.0, "crypto")
    assert p["qty"] == pytest.approx(5.0) and not p["capped"]
    # stop très serré : la taille serait énorme, plafonnée par le capital (sans levier)
    p = risk.position_size(100.0, 99.9, "stocks")
    assert p["capped"] and p["qty"] == 10                     # 1000 / 100, entier
    assert "actions" in risk.position_hint(100.0, 98.0, "stocks")
    monkeypatch.setattr(settings, "BALANCE", 0.0)
    assert risk.position_hint(100.0, 98.0) is None


def test_signaux_peu_frequents_sur_hasard_pur(monkeypatch):
    """Un moteur qui crie ACHAT/VENTE sur la moitié des bougies serait inutilisable."""
    monkeypatch.setattr(settings, "SIGNAL_MIN_POINTS", 7)
    monkeypatch.setattr(settings, "SIGNAL_MIN_MARGIN", 4)
    monkeypatch.setattr(settings, "SIGNAL_MIN_CONFIDENCE", 70)
    df = make_ohlc(3000, seed=1, drift=0.0, vol=0.002)
    s = indicators.compute_series(df)
    n = sum(se.analyze_at(df, s, i)["direction"] != "ATTENDRE" for i in range(80, len(df)))
    assert n / (len(df) - 80) < 0.35


def test_filtre_rsi_entree_tardive_sur_hausse_extreme():
    """Hausse quasi verticale (RSI > 78) : le moteur refuse d'acheter au sommet et le dit."""
    df = make_ohlc(400, seed=2, drift=0.0015, vol=0.0015)
    s = indicators.compute_series(df)
    res = [se.analyze_at(df, s, i) for i in range(80, len(df))]
    assert all(r["direction"] != "ACHAT" for r in res)
    assert any(r["blocked_by"] and "entrée acheteuse trop tardive" in r["blocked_by"] for r in res)
