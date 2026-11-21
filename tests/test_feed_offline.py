"""Couche de données testée HORS RÉSEAU (requests / yfinance simulés)."""
import pandas as pd
import pytest
import requests

from data import feed, markets
from data.feed import FeedError


def _klines(n, start_ms=1_700_000_000_000, step_ms=3_600_000):
    return [[start_ms + i * step_ms, "100", "101", "99", "100.5", "10", 0, 0, 0, 0, 0, 0]
            for i in range(n)]


class _Resp:
    def __init__(self, status=200, rows=None):
        self.status_code = status
        self._rows = rows

    def json(self):
        return self._rows


@pytest.fixture(autouse=True)
def _clean_cache():
    feed.clear_cache()
    yield
    feed.clear_cache()


def test_bougie_en_formation_supprimee():
    now = pd.Timestamp("2025-01-01 12:07", tz="UTC")
    times = pd.date_range("2025-01-01 11:30", periods=3, freq="15min", tz="UTC")  # 11:30, 11:45, 12:00
    df = pd.DataFrame({"time": times, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0})
    out = feed.drop_forming(df, "15m", now=now)
    # la bougie de 12:00 ne se ferme qu'à 12:15 : elle est en formation, on la retire
    assert out["time"].iloc[-1] == pd.Timestamp("2025-01-01 11:45", tz="UTC")


def test_bougie_fermee_conservee():
    now = pd.Timestamp("2025-01-01 12:15:01", tz="UTC")
    times = pd.date_range("2025-01-01 11:30", periods=3, freq="15min", tz="UTC")  # 11:30, 11:45, 12:00
    df = pd.DataFrame({"time": times, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0})
    assert len(feed.drop_forming(df, "15m", now=now)) == 3       # 12:00 fermée à 12:15


def test_bougie_journaliere_bourse_selon_la_cloture():
    ny = "America/New_York"
    day = pd.Timestamp("2025-03-10 00:00", tz=ny).tz_convert("UTC")
    df = pd.DataFrame({"time": [day], "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0})
    midi = pd.Timestamp("2025-03-10 12:00", tz=ny).tz_convert("UTC")
    soir = pd.Timestamp("2025-03-10 17:00", tz=ny).tz_convert("UTC")
    assert len(feed.drop_forming(df, "1d", "us", now=midi)) == 0      # séance en cours
    assert len(feed.drop_forming(df, "1d", "us", now=soir)) == 1      # séance terminée


def test_donnees_anciennes_detectees():
    t = pd.date_range("2025-01-03 10:00", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame({"time": t})
    fresh = feed.staleness(df, "1h", now=pd.Timestamp("2025-01-03 15:10", tz="UTC"))
    old = feed.staleness(df, "1h", now=pd.Timestamp("2025-01-05 15:10", tz="UTC"))   # week-end
    assert not fresh["stale"] and old["stale"]


def test_prix_fonctionne_avec_peu_de_bougies(monkeypatch):
    """v1 : /prix demandait 30 bougies alors que le minimum était 40 -> échec permanent."""
    monkeypatch.setattr(feed.requests, "get", lambda *a, **k: _Resp(200, _klines(60)))
    info = feed.fetch_price("crypto", "BTC/USDT")
    assert info["price"] > 0 and info["span"] == "sur 24h"
    assert info["change_pct"] is not None


def test_coupure_reseau_devient_feederror(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("réseau coupé")
    monkeypatch.setattr(feed.requests, "get", boom)
    monkeypatch.setattr(feed, "fetch_yfinance", lambda *a, **k: (_ for _ in ()).throw(FeedError("yahoo KO")))
    with pytest.raises(FeedError):
        feed.fetch_candles("crypto", "BTC/USDT", "5m")


def test_binance_bloque_bascule_sur_autre_hote(monkeypatch):
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return _Resp(451) if "api.binance.com" in url else _Resp(200, _klines(300))
    monkeypatch.setattr(feed.requests, "get", fake_get)
    df, src = feed.fetch_candles("crypto", "ETH/USDT", "1h")
    assert len(df) >= feed.MIN_BARS and len(calls) >= 2


def test_secours_yahoo_quand_binance_tombe(monkeypatch):
    monkeypatch.setattr(feed.requests, "get", lambda *a, **k: _Resp(500))
    t = pd.date_range("2024-01-01", periods=300, freq="1h", tz="UTC")
    yf_df = pd.DataFrame({"time": t, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 5.0})
    monkeypatch.setattr(feed, "fetch_yfinance", lambda *a, **k: yf_df)
    df, src = feed.fetch_candles("crypto", "BTC/USDT", "1h")
    assert "Yahoo" in src and len(df) >= feed.MIN_BARS


def test_timeframe_non_disponible_pour_actions():
    with pytest.raises(FeedError, match="non disponible"):
        feed.fetch_candles("stocks", "NVDA", "4h")


def test_actif_inconnu():
    with pytest.raises(FeedError):
        feed.fetch_candles("crypto", "XXX/USDT", "5m")


def test_cache_renvoie_une_copie(monkeypatch):
    monkeypatch.setattr(feed.requests, "get", lambda *a, **k: _Resp(200, _klines(300)))
    a, _ = feed.fetch_candles("crypto", "BTC/USDT", "1h")
    a.loc[:, "close"] = -1.0                      # on abîme la copie
    b, _ = feed.fetch_candles("crypto", "BTC/USDT", "1h")
    assert (b["close"] > 0).all()


def test_pas_assez_de_bougies(monkeypatch):
    monkeypatch.setattr(feed.requests, "get", lambda *a, **k: _Resp(200, _klines(20)))
    monkeypatch.setattr(feed, "fetch_yfinance", lambda *a, **k: (_ for _ in ()).throw(FeedError("x")))
    with pytest.raises(FeedError):
        feed.fetch_candles("crypto", "BTC/USDT", "1h")
