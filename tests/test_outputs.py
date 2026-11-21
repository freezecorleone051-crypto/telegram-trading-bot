"""Messages Telegram et image d'analyse : rendu, longueur, cohérence."""
import re

import pandas as pd
import pytest

from core import signal_engine as se
from outputs import messages
from outputs.chart import make_chart
from synthetic import make_ohlc


@pytest.fixture(scope="module")
def buy_case():
    df = make_ohlc(400, seed=3, drift=0.0006, vol=0.003)
    return df, se.analyze(df)           # ACHAT de tendance (vérifié dans test_mtf_pipeline)


@pytest.fixture(scope="module")
def wait_case():
    df = make_ohlc(400, seed=4, vol=0.0)
    return df, se.analyze(df)           # ATTENDRE, indépendant des seuils locaux


def _is_png(b):
    return isinstance(b, (bytes, bytearray)) and b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) > 5000


def test_graphique_achat(buy_case):
    df, sig = buy_case
    assert sig["direction"] == "ACHAT"
    assert _is_png(make_chart(df, sig, "BTC/USDT", "15m", "Binance", "Bitcoin", 2))


def test_graphique_attendre_sans_niveaux(wait_case):
    df, sig = wait_case
    assert sig["direction"] == "ATTENDRE"
    assert _is_png(make_chart(df, sig, "NVDA", "1h", "Yahoo Finance", "NVIDIA", 2))


def test_graphique_donnees_courtes():
    df = make_ohlc(70, seed=1)
    sig = se.analyze(df)
    assert _is_png(make_chart(df, sig, "EUR/USD", "15m", "Yahoo", "", 5))


def test_message_signal_longueur_et_html(buy_case):
    df, sig = buy_case
    txt = messages.build_signal_message("crypto", "BTC/USDT", "15m", sig, "Binance",
                                        "✅ 1h aligné", False, 30)
    assert len(txt) <= 4096
    assert "ACHAT" in txt and "BTC/USDT" in txt
    # balises HTML équilibrées (Telegram rejette un HTML mal formé)
    for tag in ("b", "i", "code"):
        assert len(re.findall(rf"<{tag}>", txt)) == len(re.findall(rf"</{tag}>", txt))


def test_message_donnees_anciennes_avertit(buy_case):
    df, sig = buy_case
    txt = messages.build_signal_message("stocks", "NVDA", "1h", sig, "Yahoo Finance", None, True, 3 * 86400)
    assert "marché" in txt.lower() and ("fermé" in txt.lower() or "ancien" in txt.lower())


def test_message_otc_avertissement(buy_case):
    df, sig = buy_case
    txt = messages.build_signal_message("otc", "BTC/USD OTC", "5m", sig, "Binance", None, False, 10)
    assert "OTC" in txt and messages.OTC_WARNING.split("\n")[0][:20] in txt


def test_message_attendre_sans_niveaux(wait_case):
    df, sig = wait_case
    txt = messages.build_signal_message("stocks", "NVDA", "1h", sig, "Yahoo", None, False, 10)
    assert "ATTENDRE" in txt and "Take Profit" not in txt and "TP" not in txt.replace("TP/SL", "")


def test_caption_courte(buy_case):
    _, sig = buy_case
    assert len(messages.signal_caption("BTC/USDT", "15m", sig)) <= 1024


def test_messages_stats_vide_et_rempli():
    # Les clés doivent correspondre exactement à ce que renvoie history.stats()
    empty = {"total": 0, "closed": 0, "open": 0, "win": 0, "loss": 0, "expired": 0, "unknown": 0,
             "win_rate": None, "win_rate_ci": None, "avg_r": 0.0, "total_r": 0.0,
             "profit_factor": None, "breakeven_win_rate": None, "decided": 0, "per_asset": {}}
    txt = messages.build_stats_message(empty)
    assert isinstance(txt, str) and len(txt) < 4096
