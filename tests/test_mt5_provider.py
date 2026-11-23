"""Tests hors terminal pour l'intégration lecture seule HFM/MetaTrader 5."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from config import settings
from data import feed, markets, mt5_provider


class FakeMT5:
    TIMEFRAME_M15 = 15
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_CALC_MODE_FOREX = 0
    SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE = 5
    SYMBOL_CALC_MODE_CFDINDEX = 3
    SYMBOL_CALC_MODE_EXCH_STOCKS = 32

    def __init__(self):
        self.rows = [
            SimpleNamespace(name="EURUSD", path="Forex\\Majors", description="Euro vs US Dollar",
                            trade_mode=4, digits=5, trade_calc_mode=0),
            SimpleNamespace(name="XAUUSD", path="Commodities\\Metals", description="Gold",
                            trade_mode=4, digits=2, trade_calc_mode=1),
            SimpleNamespace(name="#BTCUSD", path="Crypto", description="Bitcoin",
                            trade_mode=4, digits=2, trade_calc_mode=1),
            SimpleNamespace(name="US500", path="Indices\\Cash", description="US 500",
                            trade_mode=4, digits=1, trade_calc_mode=3),
            SimpleNamespace(name="AAPL", path="Shares\\US", description="Apple Inc.",
                            trade_mode=4, digits=2, trade_calc_mode=32),
            SimpleNamespace(name="ETF_TEST", path="ETF", description="Example ETF",
                            trade_mode=4, digits=2, trade_calc_mode=1),
            SimpleNamespace(name="DISABLED", path="Forex\\Test", description="Disabled",
                            trade_mode=0, digits=5, trade_calc_mode=0),
            SimpleNamespace(name="CFD_MISC", path="CFD\\Other", description="Other contract",
                            trade_mode=4, digits=2, trade_calc_mode=1),
        ]
        self.rates_calls = []
        self.select_calls = []

    def terminal_info(self):
        return SimpleNamespace(connected=True, company="HFM")

    def account_info(self):
        return SimpleNamespace(server="HFMarkets-Live")

    def symbols_get(self):
        return tuple(self.rows)

    def symbol_info(self, symbol):
        return SimpleNamespace(visible=False, digits=2)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.rates_calls.append((symbol, timeframe, start, count))
        times = np.arange(1_700_000_000, 1_700_000_000 + 100 * 900, 900)
        return np.array(
            [(t, 100.0, 101.0, 99.0, 100.5, 20, 1, 0)
             for t in times[-count:]],
            dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"),
                   ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
                   ("spread", "i8"), ("real_volume", "i8")],
        )

    def symbol_select(self, symbol, enabled):
        self.select_calls.append((symbol, enabled))
        return True

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(last=100.5, bid=100.4, ask=100.6, time=1_700_000_000)

    def last_error(self):
        return (0, "ok")


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr(mt5_provider, "_mt5", fake)
    monkeypatch.setattr(mt5_provider, "_symbol_rows", {})
    monkeypatch.setattr(mt5_provider, "_symbol_cache_at", 0.0)
    monkeypatch.setattr(settings, "MT5_ENABLED", True)
    monkeypatch.setattr(settings, "MT5_SERVER", "")
    monkeypatch.setattr(settings, "MT5_PATH", "")
    markets.register_hfm_assets([])
    yield fake
    markets.register_hfm_assets([])


def test_catalogue_hfm_decouvert_et_classe(fake_mt5):
    assets = mt5_provider.refresh_catalog(force=True)
    by_name = {asset.name: asset for asset in assets}

    assert set(by_name) == {"EURUSD", "XAUUSD", "#BTCUSD", "US500", "AAPL", "ETF_TEST", "CFD_MISC"}
    assert by_name["EURUSD"].market == "forex"
    assert by_name["XAUUSD"].market == "commodities"
    assert by_name["#BTCUSD"].market == "crypto"
    assert by_name["US500"].market == "indices"
    assert by_name["AAPL"].market == "stocks"
    assert by_name["ETF_TEST"].market == "etf"
    assert by_name["CFD_MISC"].market == "other"
    assert markets.get_asset("forex", "EURUSD").hfm_symbol == "EURUSD"
    assert not mt5_provider.has_symbol("forex", "DISABLED")


def test_bougies_hfm_lues_avec_symbole_exact_sans_selection_market_watch(fake_mt5):
    mt5_provider.refresh_catalog(force=True)
    frame, source = mt5_provider.fetch_candles("forex", "EURUSD", "15m", 80)

    assert len(frame) == 80
    assert pd.api.types.is_datetime64_any_dtype(frame["time"])
    assert frame["volume"].iloc[-1] == 20
    assert source == "HFM MT5 (EURUSD)"
    assert fake_mt5.rates_calls == [("EURUSD", fake_mt5.TIMEFRAME_M15, 1, 80)]
    assert fake_mt5.select_calls == []


@pytest.mark.parametrize(
    "name,path,expected",
    [
        ("#BTCUSD", "Crypto", "crypto"),
        ("#AAPL", "Shares\\US", "stocks"),
        ("ETHUSDm", "Market", "crypto"),
    ],
)
def test_classification_crypto_ne_confond_pas_symboles_prefixes(name, path, expected, fake_mt5):
    row = SimpleNamespace(name=name, path=path, description="", trade_calc_mode=1)
    assert mt5_provider._classify(row, fake_mt5) == expected


def test_prix_hfm_ajoute_la_variation_24h_depuis_les_bougies(monkeypatch, fake_mt5):
    mt5_provider.refresh_catalog(force=True)
    monkeypatch.setattr(feed, "fetch_candles", lambda *a, **k: (
        pd.DataFrame({"close": [101.0] + [100.0] * 24}), "HFM MT5 (EURUSD)"))

    info = feed.fetch_price("forex", "EURUSD")

    assert info["price"] == 100.5
    assert info["change_pct"] == pytest.approx((100.5 / 101 - 1) * 100)
    assert info["span"] == "sur 24h"
    assert info["source"] == "HFM MT5 (EURUSD)"
    assert info["live"] is True


def test_symboles_hfm_ne_basculent_pas_vers_deriv_ou_yahoo(monkeypatch, fake_mt5):
    mt5_provider.refresh_catalog(force=True)
    monkeypatch.setattr(settings, "MT5_ENABLED", True)
    monkeypatch.setattr(feed, "_fetch_mt5", lambda *a, **k: (_ for _ in ()).throw(feed.FeedError("MT5 KO")))
    monkeypatch.setattr(feed, "_fetch_deriv", lambda *a, **k: pytest.fail("Deriv ne doit pas être appelé"))
    monkeypatch.setattr(feed, "fetch_yfinance", lambda *a, **k: pytest.fail("Yahoo ne doit pas être appelé"))

    with pytest.raises(feed.FeedError, match="MT5 KO"):
        feed._provider_chain("forex", "EURUSD", "15m", 80)


def test_catalogue_desactive_quand_mt5_desactive(fake_mt5, monkeypatch):
    monkeypatch.setattr(settings, "MT5_ENABLED", False)
    assert mt5_provider.refresh_catalog(force=True) == []
    assert markets.hfm_assets() == []


def test_scan_automatique_suit_exactement_le_catalogue_hfm(fake_mt5, monkeypatch):
    from services import scanner
    from storage import history

    mt5_provider.refresh_catalog(force=True)
    monkeypatch.setattr(history, "get_watchlist", lambda: [
        ("crypto", "BTC/USDT", "15m"),
    ])
    universe = scanner._scan_universe()
    assert len(universe) == len(markets.hfm_assets())
    assert ("crypto", "#BTCUSD", "15m") in universe
    assert ("crypto", "BTC/USDT", "15m") not in universe


def test_liste_telegram_affiche_les_symboles_hfm_exacts(fake_mt5):
    from outputs.messages import build_markets_message

    mt5_provider.refresh_catalog(force=True)
    message = build_markets_message()
    assert "Catalogue de ton compte HFM MT5" in message
    assert "#BTCUSD" in message
    assert "XAUUSD" in message
    assert "BTC/USDT" not in message
