"""Catalogue des marchés et recherche floue."""
import pytest

from data import markets


@pytest.mark.parametrize("query,expected", [
    ("BTC", ("crypto", "BTC/USDT")),
    ("btcusdt", ("crypto", "BTC/USDT")),
    ("bitcoin", ("crypto", "BTC/USDT")),
    ("BTC OTC", ("otc", "BTC/USD OTC")),                  # v1 : introuvable
    ("EUR/USD OTC", ("otc", "EUR/USD OTC")),
    ("EUR/USD", ("forex", "EUR/USD")),
    ("eurusd", ("forex", "EUR/USD")),
    ("nvidia", ("stocks", "NVDA")),
    ("NVDA", ("stocks", "NVDA")),
    ("microsoft", ("stocks", "MSFT")),
    ("V", ("stocks", "V")),
    ("or", ("forex", "XAU/USD")),
    ("XAU", ("forex", "XAU/USD")),
    ("gold", ("forex", "XAU/USD")),
    ("dax", ("indices", "GER40")),
    ("S&P", ("indices", "US500")),
    ("nasdaq", ("indices", "NAS100")),
    ("spy", ("etf", "SPY")),
    ("cuivre", ("commodities", "COPPER")),
    ("petrole", ("commodities", "WTI")),
    ("argent", ("commodities", "XAG/USD")),
])
def test_find_asset(query, expected):
    assert markets.find_asset(query) == expected


def test_find_asset_inconnu():
    assert markets.find_asset("zzzzqq") is None
    assert markets.find_asset("") is None


def test_catalogue_coherent():
    seen = set()
    for market in markets.MARKET_ORDER:
        for a in markets.CATALOG[market]:
            assert (market, a.name) not in seen
            seen.add((market, a.name))
            assert a.market == market
            assert a.yf or a.binance, f"{a.name} sans source de cours"
            if a.session:
                assert a.session in markets.SESSIONS
        for tf in markets.timeframes_for(market):
            assert tf in markets.TIMEFRAMES
            assert tf in markets.fetchable_timeframes(market)
    # noms d'actifs uniques à travers les familles (sinon find_asset serait ambigu)
    names = [a.name for m in markets.MARKET_ORDER for a in markets.CATALOG[m]]
    assert len(names) == len(set(names))
    assert len(names) >= 70


def test_mtf_map_marches_a_seances_sans_4h():
    for tf, chain in markets.MTF_MAP_SESSION.items():
        assert "4h" not in chain
    for market in markets.SESSION_MARKETS:
        assert "4h" not in markets.fetchable_timeframes(market)


def test_parse_timeframe_et_marche():
    assert markets.parse_timeframe("H4") == "4h"
    assert markets.parse_timeframe("D") == "1d"
    assert markets.parse_timeframe("nvidia") is None
    assert markets.parse_market("Actions") == "stocks"
    assert markets.parse_market("matières") == "commodities"


def test_decimales_forex():
    assert markets.decimals_for("forex", "EUR/USD") == 5
    assert markets.decimals_for("forex", "USD/JPY") == 3
    assert markets.decimals_for("stocks", "NVDA") == 2
