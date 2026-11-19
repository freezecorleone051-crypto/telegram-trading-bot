"""
Récupération des chandeliers (bougies) avec cache TTL et système de secours.

- crypto  -> API publique Binance (aucune clé, gratuit)
- forex   -> Yahoo Finance via yfinance (EURUSD=X, GC=F pour l'or...)
- otc     -> routé vers Binance ou yfinance selon l'actif
            (le graphique est identique à celui du broker : même actif,
             même marché sous-jacent)

Toutes les fonctions renvoient un DataFrame pandas avec les colonnes :
time (tz-aware, heure locale), open, high, low, close, volume
"""
import threading
import time as _time

import pandas as pd
import requests

from config import settings
from data import markets

BINANCE_URL = "https://api.binance.com/api/v3/klines"
REQUEST_TIMEOUT = 15


class FeedError(Exception):
    """Levée quand aucune source ne répond avec assez de données."""


# ------------------------------------------------------------------
# Cache mémoire simple (TTL court, évite de marteler les API
# quand le scan + l'analyse MTF tapent les mêmes actifs)
# ------------------------------------------------------------------
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and _time.time() - item[0] < settings.FEED_CACHE_TTL:
            return item[1], item[2]
    return None


def _cache_put(key, df, label):
    with _cache_lock:
        _cache[key] = (_time.time(), df, label)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ------------------------------------------------------------------
# Binance (crypto + OTC crypto)
# ------------------------------------------------------------------
def fetch_binance(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_URL, params=params, timeout=REQUEST_TIMEOUT,
                     headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        raise FeedError(f"Binance HTTP {r.status_code}")
    rows = r.json()
    if not rows:
        raise FeedError("Binance : réponse vide")
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "ntrades", "tbb", "tbq", "ignore"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["time", "open", "high", "low", "close", "volume"]]


# ------------------------------------------------------------------
# Yahoo Finance (forex + or + crypto de secours)
# ------------------------------------------------------------------
def fetch_yfinance(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    import yfinance as yf  # import paresseux pour démarrer plus vite

    yf_interval, period = markets.YF_INTERVAL_MAP.get(interval, ("5m", "1mo"))
    ticker = yf.Ticker(symbol)
    df = ticker.history(interval=yf_interval, period=period,
                        auto_adjust=False, prepost=False)
    if df is None or df.empty:
        raise FeedError(f"Yahoo : aucune donnée pour {symbol}")

    df = df.reset_index()
    rename = {}
    for col in df.columns:
        low = str(col).lower()
        if low.startswith("date") or low.startswith("datetime") or low == "index":
            rename[col] = "time"
        elif low in ("open", "high", "low", "close"):
            rename[col] = low
        elif low in ("volume", "vol"):
            rename[col] = "volume"
    df = df.rename(columns=rename)

    needed = {"time", "open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        raise FeedError(f"Yahoo : colonnes inattendues {list(df.columns)}")
    if "volume" not in df.columns:
        df["volume"] = 0.0

    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"])

    # Timeframe 4h : recalculée à partir des bougies 1h
    if interval == "4h":
        df = df.set_index("time").resample("4h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).dropna().reset_index()

    df = df.sort_values("time").tail(limit).reset_index(drop=True)
    if len(df) < 30:
        raise FeedError(f"Yahoo : seulement {len(df)} bougies pour {symbol}")
    return df[["time", "open", "high", "low", "close", "volume"]]


# ------------------------------------------------------------------
# Routage principal
# ------------------------------------------------------------------
def fetch_candles(market: str, asset: str, timeframe: str, limit: int = 250):
    """
    Renvoie (dataframe, source_label), avec cache court.
    market : 'crypto' | 'forex' | 'otc'
    asset  : ex. 'BTC/USDT', 'EUR/USD', 'BTC/USD OTC'
    timeframe : '1m' '5m' '15m' '1h' '4h'
    """
    timeframe = timeframe.lower()
    if timeframe not in markets.TIMEFRAMES:
        raise FeedError(f"Timeframe inconnu : {timeframe}")

    key = (market, asset, timeframe, limit)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    errors = []

    if market == "crypto":
        sym = asset.replace("/", "")
        try:
            df = fetch_binance(sym, timeframe, limit)
            result = (_finalize(df), f"Binance ({sym})")
            _cache_put(key, *result)
            return result
        except FeedError as e:
            errors.append(f"Binance : {e}")
            yf_sym = markets.CRYPTO_YF_FALLBACK.get(asset)
            if yf_sym:
                try:
                    df = fetch_yfinance(yf_sym, timeframe, limit)
                    result = (_finalize(df), f"Yahoo Finance ({yf_sym}, secours)")
                    _cache_put(key, *result)
                    return result
                except FeedError as e2:
                    errors.append(f"Yahoo : {e2}")

    elif market == "forex":
        sym = markets.FOREX_SYMBOLS.get(asset, asset.replace("/", "") + "=X")
        candidates = [sym] + markets.FOREX_FALLBACKS.get(asset, [])
        for cand in candidates:
            try:
                df = fetch_yfinance(cand, timeframe, limit)
                result = (_finalize(df), f"Yahoo Finance ({cand})")
                _cache_put(key, *result)
                return result
            except FeedError as e:
                errors.append(f"{cand} : {e}")

    elif market == "otc":
        source, sym = markets.OTC_ROUTE.get(asset, (None, None))
        if source == "binance":
            try:
                df = fetch_binance(sym, timeframe, limit)
                result = (_finalize(df), f"Binance ({sym}) — même actif que l'OTC")
                _cache_put(key, *result)
                return result
            except FeedError as e:
                errors.append(f"Binance : {e}")
                # secours : le même actif en crypto chez Yahoo (BTC-USD, ETH-USD)
                yf_sym = {"BTC/USD OTC": "BTC-USD", "ETH/USD OTC": "ETH-USD"}.get(asset)
                if yf_sym:
                    try:
                        df = fetch_yfinance(yf_sym, timeframe, limit)
                        result = (_finalize(df),
                                  f"Yahoo Finance ({yf_sym}, secours) — même actif que l'OTC")
                        _cache_put(key, *result)
                        return result
                    except FeedError as e2:
                        errors.append(f"Yahoo : {e2}")
        elif source == "yfinance":
            df = fetch_yfinance(sym, timeframe, limit)
            result = (_finalize(df), f"Yahoo Finance ({sym}) — même actif que l'OTC")
            _cache_put(key, *result)
            return result
        errors.append("Actif OTC inconnu")

    raise FeedError(" / ".join(errors) if errors else f"Marché inconnu : {market}")


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("time").tail(250).reset_index(drop=True)
    if len(df) < 40:
        raise FeedError(f"Pas assez de bougies ({len(df)}) pour analyser")
    # Heure locale (Lubumbashi) pour l'affichage sur les graphiques
    df["time"] = df["time"].dt.tz_convert(settings.TZ_NAME)
    return df


if __name__ == "__main__":
    # Test manuel : python -m data.feed
    t0 = _time.time()
    for mkt, name, tf in [("crypto", "BTC/USDT", "5m"),
                          ("forex", "EUR/USD", "5m"),
                          ("otc", "BTC/USD OTC", "5m")]:
        try:
            data, src = fetch_candles(mkt, name, tf)
            print(f"[OK] {mkt:6s} {name:14s} {tf:3s} -> {len(data)} bougies "
                  f"({src}) en {_time.time()-t0:.1f}s "
                  f"| dernière bougie {data['time'].iloc[-1]}")
        except FeedError as err:
            print(f"[ERREUR] {mkt} {name} {tf} -> {err}")
