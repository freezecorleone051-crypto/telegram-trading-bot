"""Contrôle multi-timeframe et pipeline d'analyse (avec cours simulés)."""
import pytest

from config import settings
from data import feed
from services import mtf
from services.analysis import full_analysis
from synthetic import make_ohlc


def _r(tf, trend, available=True):
    return {"tf": tf, "available": available, "direction": "X", "bias": "NEUTRE",
            "trend": trend, "regime": "tendance", "confidence": 70}


def test_timeframes_superieurs_neutres_ne_sont_pas_contre():
    """v1 : deux timeframes ATTENDRE étaient présentés comme « contre le signal »."""
    v = mtf.assess([_r("15m", "haussière"), _r("1h", "neutre"), _r("4h", "neutre")], "ACHAT", "strict")
    assert v["status"] != "against" and not v["blocks"]
    assert "CONTRE" not in v["text"]


def test_tf_superieur_immediat_contre_bloque_en_strict_seulement():
    res = [_r("15m", "haussière"), _r("1h", "baissière"), _r("4h", "baissière")]
    assert mtf.assess(res, "ACHAT", "strict")["blocks"]
    assert not mtf.assess(res, "ACHAT", "soft")["blocks"]
    assert not mtf.assess(res, "ACHAT", "off")["blocks"]
    assert mtf.assess(res, "ACHAT", "strict")["status"] == "against"


def test_alignement_total():
    v = mtf.assess([_r("15m", "baissière"), _r("1h", "baissière"), _r("4h", "baissière")], "VENTE", "strict")
    assert v["status"] == "aligned" and not v["blocks"]


def test_divergence_partielle_ne_bloque_pas():
    v = mtf.assess([_r("15m", "haussière"), _r("1h", "haussière"), _r("4h", "baissière")], "ACHAT", "strict")
    assert v["status"] == "partial" and not v["blocks"]


def test_aucun_tf_superieur():
    v = mtf.assess([_r("1w", "haussière")], "ACHAT", "strict")
    assert v["status"] == "none" and not v["blocks"]


def test_tf_indisponible_toleres():
    res = [_r("15m", "haussière"), _r("1h", "neutre", available=False)]
    assert mtf.assess(res, "ACHAT", "strict")["status"] == "none"


def _fake_fetch_factory(up_tf="15m", others=None):
    """Cours simulés : tendance haussière sur `up_tf`, baissière ailleurs."""
    # seed 3 / dérive +0,06 % : ACHAT de tendance sur la dernière bougie (vérifié)
    data = {"up": make_ohlc(400, seed=3, drift=0.0006, vol=0.003),
            "down": make_ohlc(400, seed=6, drift=-0.0006, vol=0.003)}
    data["up"]["time"] = data["up"]["time"].dt.tz_convert("Africa/Lubumbashi")
    data["down"]["time"] = data["down"]["time"].dt.tz_convert("Africa/Lubumbashi")

    def fetch(market, asset, tf, limit=250, **k):
        return (data["up"] if tf == up_tf else data["down"]).copy(), "simulé"
    return fetch


def test_pipeline_analyse_complete():
    res = full_analysis("crypto", "BTC/USDT", "15m", "always", fetch=_fake_fetch_factory("15m"))
    assert res["signal"]["direction"] in ("ACHAT", "VENTE", "ATTENDRE")
    assert res["mtf"] and res["mtf"][0]["tf"] == "15m"
    assert res["stale"] is True or res["stale"] is False


def test_pipeline_filtre_mtf_strict_annule_un_signal_de_tendance(monkeypatch):
    monkeypatch.setattr(settings, "MTF_FILTER", "strict")
    # 15m haussier (signal ACHAT de tendance) ; 1h et 4h baissiers -> annulé
    res = full_analysis("crypto", "BTC/USDT", "15m", "always", fetch=_fake_fetch_factory("15m"))
    sig = res["signal"]
    assert sig["raw_direction"] == "ACHAT" and sig["regime"] == "tendance"
    assert sig["direction"] == "ATTENDRE" and "multi-timeframe" in sig["blocked_by"]
    assert res["mtf_status"] == "against"


def test_pipeline_mtf_off_garde_le_signal(monkeypatch):
    monkeypatch.setattr(settings, "MTF_FILTER", "off")
    res = full_analysis("crypto", "BTC/USDT", "15m", "always", fetch=_fake_fetch_factory("15m"))
    assert res["signal"]["raw_direction"] == "ACHAT"
    assert res["signal"]["direction"] == "ACHAT"


def test_pipeline_signal_only_ne_charge_pas_le_mtf_sans_signal():
    calls = []

    def fetch(market, asset, tf, limit=250, **k):
        calls.append(tf)
        n = 200
        import pandas as pd
        df = pd.DataFrame({"time": pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC"),
                           "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0})
        return df, "plat"
    res = full_analysis("crypto", "BTC/USDT", "15m", "signal_only", fetch=fetch)
    assert res["signal"]["direction"] == "ATTENDRE" and calls == ["15m"] and res["mtf"] is None


def test_pipeline_propage_feederror():
    def fetch(*a, **k):
        raise feed.FeedError("down")
    with pytest.raises(feed.FeedError):
        full_analysis("crypto", "BTC/USDT", "15m", "always", fetch=fetch)
