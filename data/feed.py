"""Market data providers and candle handling.

Deriv is queried first across all supported market families. Binance and Yahoo
Finance remain fallbacks for unlisted instruments or provider outages.
"""
import json
import logging
import threading
import time

import pandas as pd
import requests
import websockets.sync.client
import yfinance as yf

from config import settings
from data import markets

log = logging.getLogger("tradingbot.feed")

MIN_BARS = 40
MAX_BARS = 500
BINANCE_HOSTS = (
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
)
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"
DERIV_SYMBOL_TTL = 3600

_cache = {}
_cache_lock = threading.Lock()
_deriv_symbols_cache = (0.0, ())
_deriv_symbols_lock = threading.Lock()


class FeedError(RuntimeError):
    """Raised when no provider can return enough valid candles."""


def clear_cache(market=None, asset=None):
    """Clear all cached candles, or just those belonging to one asset."""
    with _cache_lock:
        if market is None or asset is None:
            _cache.clear()
            return
        for key in list(_cache):
            if key[0] == market and key[1] == asset:
                del _cache[key]


def _normalise_frame(frame):
    if frame is None or frame.empty:
        raise FeedError("aucune bougie reçue")

    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [column[0] for column in frame.columns]
    frame.columns = [str(column).strip().lower() for column in frame.columns]

    if "time" not in frame.columns:
        frame = frame.reset_index()
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        if "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "time"})
        elif "date" in frame.columns:
            frame = frame.rename(columns={"date": "time"})
        elif "index" in frame.columns:
            frame = frame.rename(columns={"index": "time"})

    required = ("time", "open", "high", "low", "close")
    if any(column not in frame.columns for column in required):
        raise FeedError("format de bougies invalide")
    if "volume" not in frame.columns:
        frame["volume"] = 0.0

    frame = frame.loc[:, ["time", "open", "high", "low", "close", "volume"]]
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame = frame.sort_values("time").drop_duplicates("time", keep="last")
    return frame.reset_index(drop=True)


def _binance_interval(timeframe):
    return {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h",
            "4h": "4h", "1d": "1d", "1w": "1w"}[timeframe]


def _fetch_binance(market, asset, timeframe, limit):
    item = markets.get_asset(market, asset)
    symbol = item.binance if item else None
    if market == "otc":
        route = markets.OTC_ROUTE.get(asset)
        symbol = route[1] if route and route[0] == "binance" else None
    if not symbol:
        raise FeedError("actif non disponible chez Binance")

    errors = []
    for host in BINANCE_HOSTS:
        try:
            response = requests.get(
                f"{host}/api/v3/klines",
                params={"symbol": symbol, "interval": _binance_interval(timeframe),
                        "limit": min(max(int(limit), MIN_BARS), 1000)},
                timeout=(3, 8),
            )
            if response.status_code != 200:
                errors.append(f"{host}: HTTP {response.status_code}")
                continue
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                errors.append(f"{host}: réponse sans bougies")
                continue
            frame = pd.DataFrame(
                [[row[0], row[1], row[2], row[3], row[4], row[5]]
                 for row in rows],
                columns=["time", "open", "high", "low", "close", "volume"],
            )
            frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
            return _normalise_frame(frame), "Binance"
        except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
            errors.append(f"{host}: {type(exc).__name__}")
    raise FeedError("Binance indisponible (" + "; ".join(errors) + ")")


def _deriv_request(connection, payload):
    connection.send(json.dumps(payload))
    response = json.loads(connection.recv(timeout=8))
    if "error" in response:
        raise FeedError(response["error"].get("message", "erreur API Deriv"))
    return response


def _get_deriv_symbols():
    global _deriv_symbols_cache
    now = time.monotonic()
    with _deriv_symbols_lock:
        cached_at, symbols = _deriv_symbols_cache
        if symbols and now - cached_at < DERIV_SYMBOL_TTL:
            return symbols

    with websockets.sync.client.connect(
            DERIV_WS_URL.format(app_id=settings.DERIV_APP_ID),
            open_timeout=5, close_timeout=2, max_size=2**20) as connection:
        response = _deriv_request(connection, {"active_symbols": "brief"})
    symbols = response.get("active_symbols")
    if not isinstance(symbols, list):
        raise FeedError("réponse active_symbols invalide")
    with _deriv_symbols_lock:
        _deriv_symbols_cache = (time.monotonic(), tuple(symbols))
    return tuple(symbols)


def _resolve_deriv_symbol(market, asset, symbols):
    item = markets.get_asset(market, asset)
    if item is None:
        return None

    underlying_name = item.name
    if market == "otc" and underlying_name.upper().endswith(" OTC"):
        underlying_name = underlying_name[:-4].strip()
    display_names = {
        _symbol_key(value) for value in (item.name, item.label, *item.aliases)
        if value
    }
    display_names.add(_symbol_key(underlying_name))
    crypto_base = (item.binance[:-4] if item.binance and
                   item.binance.endswith("USDT") else None)
    pair_tail = (_symbol_key(f"{crypto_base}USD") if crypto_base
                 else _symbol_key(underlying_name))
    if "/" in underlying_name:
        display_names.add(pair_tail)

    exact_display = []
    exact_symbol = []
    for row in symbols:
        symbol = str(row.get("symbol", ""))
        display = str(row.get("display_name", ""))
        if _symbol_key(display) in display_names:
            exact_display.append(symbol)
            continue
        normalized_symbol = _symbol_key(symbol)
        if ("/" in underlying_name and pair_tail
                and normalized_symbol.endswith(pair_tail)):
            exact_symbol.append(symbol)
        elif normalized_symbol in display_names:
            exact_symbol.append(symbol)

    matches = exact_display or exact_symbol
    unique = list(dict.fromkeys(symbol for symbol in matches if symbol))
    if len(unique) > 1 and ("/" in underlying_name or crypto_base):
        prefix = "cry" if crypto_base else "frx"
        preferred = [symbol for symbol in unique
                     if symbol.upper().startswith(prefix.upper())]
        if len(preferred) == 1:
            return preferred[0]
    return unique[0] if len(unique) == 1 else None


def _symbol_key(value):
    return "".join(character for character in str(value).upper()
                   if character.isalnum())


def _fetch_deriv(market, asset, timeframe, limit):
    if not settings.DERIV_APP_ID:
        raise FeedError("DERIV_APP_ID n'est pas configuré")
    granularity = markets.TF_SECONDS[timeframe]
    if timeframe == "1w":
        granularity = 86400

    symbols = _get_deriv_symbols()
    symbol = _resolve_deriv_symbol(market, asset, symbols)
    if not symbol:
        raise FeedError(f"{asset} n'est pas disponible chez Deriv")

    with websockets.sync.client.connect(
            DERIV_WS_URL.format(app_id=settings.DERIV_APP_ID),
            open_timeout=5, close_timeout=2, max_size=2**20) as connection:
        response = _deriv_request(connection, {
            "ticks_history": symbol,
            "end": "latest",
            "count": min(max(int(limit), MIN_BARS), 5000),
            "style": "candles",
            "granularity": granularity,
        })

    candles = response.get("candles")
    if not isinstance(candles, list) or not candles:
        raise FeedError("Deriv n'a renvoyé aucune bougie")
    frame = _normalise_frame(pd.DataFrame(candles).rename(columns={"epoch": "time"}))
    if timeframe == "1w":
        frame = _resample(frame, "1w")
    return frame, "Deriv"


def _fetch_deriv_tick(market, asset):
    """Fetch one current Deriv tick without subscribing to a long-lived stream."""
    if not settings.DERIV_APP_ID:
        raise FeedError("DERIV_APP_ID n'est pas configuré")
    try:
        symbol = _resolve_deriv_symbol(market, asset, _get_deriv_symbols())
        if not symbol:
            raise FeedError(f"{asset} n'est pas disponible chez Deriv")
        with websockets.sync.client.connect(
                DERIV_WS_URL.format(app_id=settings.DERIV_APP_ID),
                open_timeout=5, close_timeout=2, max_size=2**20) as connection:
            response = _deriv_request(connection, {"ticks": symbol})
    except FeedError:
        raise
    except Exception as exc:
        raise FeedError(f"flux temps réel Deriv indisponible ({type(exc).__name__})") from exc

    tick = response.get("tick")
    if not isinstance(tick, dict) or tick.get("quote") is None:
        raise FeedError("Deriv n'a renvoyé aucun tick")
    try:
        price = float(tick["quote"])
    except (TypeError, ValueError) as exc:
        raise FeedError("prix Deriv invalide") from exc
    return {"price": price, "epoch": tick.get("epoch")}


def _resample(frame, timeframe):
    rule = {"4h": "4h", "1w": "W-SUN"}[timeframe]
    indexed = frame.set_index("time")
    grouped = indexed.resample(rule, label="left", closed="left")
    result = grouped.agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    result.index.name = "time"
    return _normalise_frame(result.reset_index())


def fetch_yfinance(market, asset, timeframe, limit=MAX_BARS):
    item = markets.get_asset(market, asset)
    symbols = []
    if market == "otc":
        route = markets.OTC_ROUTE.get(asset)
        if route and route[0] == "yfinance":
            symbols.append(route[1])
    elif item and item.yf:
        symbols.append(item.yf)
    if item:
        symbols.extend(item.yf_fallbacks)
    if market == "crypto":
        fallback = markets.CRYPTO_YF_FALLBACK.get(asset)
        if fallback:
            symbols.append(fallback)
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise FeedError("aucun symbole Yahoo Finance pour cet actif")

    interval, period = markets.YF_INTERVAL_MAP[timeframe]
    if timeframe == "4h":
        interval = "1h"

    errors = []
    for symbol in symbols:
        try:
            frame = yf.download(
                symbol, period=period, interval=interval, progress=False,
                auto_adjust=False, threads=False, timeout=10,
            )
            if frame is None or frame.empty:
                errors.append(f"{symbol}: aucune donnée")
                continue
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            frame = frame.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            frame.index.name = "time"
            frame = _normalise_frame(frame.reset_index())
            if timeframe == "4h":
                frame = _resample(frame, "4h")
            return frame.tail(limit), f"Yahoo Finance ({symbol})"
        except Exception as exc:
            errors.append(f"{symbol}: {type(exc).__name__}")
    raise FeedError("Yahoo Finance indisponible (" + "; ".join(errors) + ")")


def _provider_chain(market, asset, timeframe, limit):
    providers = [_fetch_deriv]
    if market == "crypto" or (market == "otc" and
                              markets.OTC_ROUTE.get(asset, (None,))[0] == "binance"):
        providers.append(_fetch_binance)
    providers.append(fetch_yfinance)

    errors = []
    for provider in providers:
        try:
            result = provider(market, asset, timeframe, limit)
            if isinstance(result, tuple) and len(result) == 2:
                frame, source = result
            else:
                frame, source = result, "Yahoo Finance"
            frame = drop_forming(frame, timeframe, _session_for(market, asset))
            if len(frame) < MIN_BARS:
                raise FeedError(f"{len(frame)} bougies valides, {MIN_BARS} requises")
            return frame.tail(limit).reset_index(drop=True), source
        except FeedError as exc:
            errors.append(f"{provider.__name__}: {exc}")
        except Exception as exc:
            log.warning("Source de cours %s en erreur pour %s: %s",
                        provider.__name__, asset, exc)
            errors.append(f"{provider.__name__}: {type(exc).__name__}")
    raise FeedError("Aucune source disponible. " + " | ".join(errors))


def fetch_candles(market, asset, timeframe, limit=MAX_BARS):
    """Return (closed OHLCV candles, provider name) using provider failover."""
    if market not in markets.CATALOG or asset not in markets.assets_for(market):
        raise FeedError(f"actif inconnu : {market}/{asset}")
    if timeframe not in markets.fetchable_timeframes(market):
        raise FeedError(f"timeframe {timeframe} non disponible pour {market}")
    limit = max(MIN_BARS, min(int(limit), MAX_BARS))
    key = (market, asset, timeframe, limit)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < settings.FEED_CACHE_TTL:
            return cached[1].copy(), cached[2]

    frame, source = _provider_chain(market, asset, timeframe, limit)
    with _cache_lock:
        _cache[key] = (time.monotonic(), frame.copy(), source)
    return frame.copy(), source


def fetch_history(market, asset, timeframe, bars):
    return fetch_candles(market, asset, timeframe, bars)


def fetch_price(market, asset):
    hourly = None
    history_source = None
    history_error = None
    try:
        hourly, history_source = fetch_candles(market, asset, "1h", 48)
    except FeedError as exc:
        history_error = exc

    try:
        tick = _fetch_deriv_tick(market, asset)
        current = tick["price"]
        source = "Deriv"
        live = True
    except FeedError as exc:
        log.info("Tick Deriv indisponible pour %s: %s", asset, exc)
        try:
            recent, source = fetch_candles(market, asset, "1m", 60)
        except FeedError:
            if hourly is None:
                raise FeedError(f"prix indisponible pour {asset}: {history_error or exc}") from exc
            recent, source = hourly, history_source
        current = float(recent["close"].iloc[-1])
        live = False

    change_pct = None
    if hourly is not None and len(hourly) >= 2:
        close = hourly["close"].astype(float)
        previous = float(close.iloc[max(0, len(close) - 25)])
        if previous:
            change_pct = ((current / previous) - 1) * 100
    if live and history_source and history_source != "Deriv":
        source = f"Deriv (tick live), variation {history_source}"
    elif not live:
        source = f"{source} (dernière bougie)"
    return {"price": current, "change_pct": change_pct,
            "span": "sur 24h", "source": source, "live": live}


def _session_for(market, asset):
    item = markets.get_asset(market, asset)
    return item.session if item and item.session else None


def drop_forming(frame, timeframe, session=None, now=None):
    """Remove a candle whose close boundary has not yet passed."""
    if frame.empty:
        return frame.copy()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    result = frame.copy()
    last_time = pd.Timestamp(result["time"].iloc[-1])
    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    else:
        last_time = last_time.tz_convert("UTC")

    if timeframe == "1d" and session in markets.SESSIONS:
        timezone, close_hour, close_minute = markets.SESSIONS[session]
        local_now = now.tz_convert(timezone)
        local_candle_date = last_time.tz_convert(timezone).date()
        if (local_candle_date == local_now.date()
                or last_time.date() == local_now.date()):
            close_time = local_now.replace(
                hour=close_hour, minute=close_minute, second=0, microsecond=0)
            if local_now < close_time:
                return result.iloc[:-1].reset_index(drop=True)
        return result.reset_index(drop=True)

    seconds = markets.TF_SECONDS.get(timeframe)
    if seconds and last_time + pd.Timedelta(seconds=seconds) > now:
        return result.iloc[:-1].reset_index(drop=True)
    return result.reset_index(drop=True)


def staleness(frame, timeframe, now=None):
    """Report candle age and whether it is stale relative to its timeframe."""
    if frame.empty:
        return {"stale": True, "age_s": None}
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    last_time = pd.Timestamp(frame["time"].iloc[-1])
    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    else:
        last_time = last_time.tz_convert("UTC")
    age = max(0.0, (now - last_time).total_seconds())
    allowed_age = markets.TF_SECONDS.get(timeframe, 86400) * 2.5
    return {"stale": age > allowed_age, "age_s": age}
