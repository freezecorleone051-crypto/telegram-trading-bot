"""
Récupération des chandeliers (bougies) avec cache TTL et système de secours.

- crypto  -> API publique Binance (aucune clé, gratuit), secours Yahoo Finance
- forex, actions, ETF, indices, matières premières -> Yahoo Finance (yfinance)
- otc     -> même actif réel (Binance pour la crypto, Yahoo sinon)

Toutes les fonctions renvoient un DataFrame pandas avec les colonnes :
time (tz-aware, heure locale), open, high, low, close, volume

Règles de sécurité appliquées ici (et pas ailleurs) :
- la bougie EN COURS DE FORMATION est supprimée : le moteur ne travaille que
  sur des bougies FERMÉES, sinon un signal peut apparaître puis disparaître
  avant la clôture (« repaint ») ;
- toute erreur réseau / API est convertie en FeedError (jamais d'exception
  brute qui planterait un scan) ;
- les données périmées (marché fermé) sont repérables via `staleness()`.
"""
import logging
import threading
import time as _time
import pandas as pd
import requests

from config import settings
from data import markets

log = logging.getLogger("tradingbot.feed")

# Plusieurs points d'accès Binance : le premier qui répond est mémorisé.
# (api.binance.com est bloqué dans certains pays ; data-api.binance.vision est
# le point d'accès public « données de marché » de Binance.)
BINANCE_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision",
                 "https://api1.binance.com", "https://api2.binance.com"]
BINANCE_PATH = "/api/v3/klines"
REQUEST_TIMEOUT = 10
MIN_BARS = 60          # minimum de bougies fermées pour analyser (EMA50, ADX...)
MAX_BARS = 250         # fenêtre analysée par défaut

_preferred_host = 0
_yf_lock = threading.Lock()   # yfinance n'est pas sûr en multi-thread


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
            return item[1].copy(), item[2]
    return None


def _cache_put(key, df, label):
    with _cache_lock:
        _cache[key] = (_time.time(), df.copy(), label)
        if len(_cache) > 400:      # garde-fou mémoire
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]
            for k, _ in oldest:
                _cache.pop(k, None)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ------------------------------------------------------------------
# Binance (crypto + OTC crypto)
# ------------------------------------------------------------------
def _binance_request(params: dict) -> list:
    """GET /klines avec bascule automatique d'un point d'accès à l'autre."""
    global _preferred_host
    errors = []
    n = len(BINANCE_HOSTS)
    for k in range(n):
        idx = (_preferred_host + k) % n
        host = BINANCE_HOSTS[idx]
        try:
            r = requests.get(host + BINANCE_PATH, params=params,
                             timeout=REQUEST_TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0"})
        except requests.exceptions.RequestException as e:
            errors.append(f"{host.split('//')[1]} : {type(e).__name__}")
            continue
        if r.status_code in (418, 429):
            # limite de débit / ban IP : inutile d'insister sur un autre hôte
            raise FeedError(f"Binance limite de débit (HTTP {r.status_code})")
        if r.status_code != 200:
            errors.append(f"{host.split('//')[1]} : HTTP {r.status_code}")
            continue
        try:
            rows = r.json()
        except ValueError:
            errors.append(f"{host.split('//')[1]} : réponse illisible")
            continue
        _preferred_host = idx
        return rows
    raise FeedError("Binance injoignable (" + " ; ".join(errors) + ")")


def _binance_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "ntrades", "tbb", "tbq", "ignore"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["time", "open", "high", "low", "close", "volume"]]


def fetch_binance(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    rows = _binance_request({"symbol": symbol, "interval": interval,
                             "limit": min(max(limit, 2), 1000)})
    if not rows:
        raise FeedError("Binance : réponse vide")
    return _binance_df(rows)


def fetch_binance_history(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    """Historique long (backtest) : remonte dans le temps par blocs de 1000."""
    frames, end = [], None
    remaining = bars
    while remaining > 0:
        params = {"symbol": symbol, "interval": interval,
                  "limit": min(1000, remaining)}
        if end is not None:
            params["endTime"] = end
        rows = _binance_request(params)
        if not rows:
            break
        df = _binance_df(rows)
        frames.append(df)
        remaining -= len(df)
        end = int(rows[0][0]) - 1
        if len(rows) < params["limit"]:
            break
    if not frames:
        raise FeedError("Binance : aucun historique")
    return pd.concat(frames).drop_duplicates("time").sort_values("time")


# ------------------------------------------------------------------
# Yahoo Finance (forex, actions, ETF, indices, matières premières)
# ------------------------------------------------------------------
def fetch_yfinance(symbol: str, interval: str, limit: int = 250,
                   period: str | None = None,
                   naive_tz: str = "UTC") -> pd.DataFrame:
    try:
        import yfinance as yf  # import paresseux pour démarrer plus vite
    except ImportError as e:  # pragma: no cover
        raise FeedError("yfinance n'est pas installé (pip install yfinance)") from e

    yf_interval, default_period = markets.YF_INTERVAL_MAP.get(interval, ("5m", "1mo"))
    period = period or default_period
    df = None
    last_err = None
    for attempt in range(2):          # 1 nouvel essai en cas d'erreur passagère
        try:
            with _yf_lock:
                df = yf.Ticker(symbol).history(interval=yf_interval, period=period,
                                               auto_adjust=False, prepost=False)
            if df is not None and not df.empty:
                break
        except Exception as e:  # yfinance lève des erreurs très variées
            last_err = e
        _time.sleep(0.6)
    if df is None or df.empty:
        detail = f" ({type(last_err).__name__})" if last_err else ""
        raise FeedError(f"Yahoo : aucune donnée pour {symbol}{detail}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    rename = {}
    for col in df.columns:
        low = str(col).lower()
        if low.startswith("date") or low == "index":
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

    t = pd.to_datetime(df["time"], errors="coerce")
    if t.dt.tz is None:     # anciennes versions : dates sans fuseau
        t = t.dt.tz_localize(naive_tz, ambiguous="NaT", nonexistent="NaT")
    df["time"] = t.dt.tz_convert("UTC")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"])

    # Timeframe 4h : recalculée à partir des bougies 1h (UTC)
    if interval == "4h":
        df = df.set_index("time").resample("4h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).dropna(
            subset=["open", "close"]).reset_index()

    return df[["time", "open", "high", "low", "close", "volume"]].sort_values(
        "time").reset_index(drop=True)


# ------------------------------------------------------------------
# Bougies fermées, fraîcheur des données
# ------------------------------------------------------------------
def tf_seconds(tf: str) -> int:
    return markets.TF_SECONDS.get(tf, 300)


def _is_complete(open_ts: pd.Timestamp, tf: str, now: pd.Timestamp,
                 session: str | None) -> bool:
    """Une bougie est complète quand son intervalle est écoulé."""
    if tf == "1d" and session in markets.SESSIONS:
        tz, hh, mm = markets.SESSIONS[session]
        lo, ln = open_ts.tz_convert(tz), now.tz_convert(tz)
        if ln.date() > lo.date():
            return True
        if ln.date() == lo.date():
            return ln.hour * 60 + ln.minute >= hh * 60 + mm
        return False
    return open_ts + pd.Timedelta(seconds=tf_seconds(tf)) <= now


def drop_forming(df: pd.DataFrame, tf: str, session: str | None = None,
                 now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Retire la dernière bougie si elle n'est pas encore fermée."""
    if df.empty:
        return df
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    last_open = pd.Timestamp(df["time"].iloc[-1])
    if last_open.tzinfo is None:
        last_open = last_open.tz_localize("UTC")
    if _is_complete(last_open, tf, now, session):
        return df
    return df.iloc[:-1].reset_index(drop=True)


def staleness(df: pd.DataFrame, tf: str, now: pd.Timestamp | None = None) -> dict:
    """Fraîcheur des données : age en secondes depuis la fermeture de la
    dernière bougie, et `stale` = True si le marché semble fermé / le flux gelé."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    last_open = pd.Timestamp(df["time"].iloc[-1])
    if last_open.tzinfo is None:
        last_open = last_open.tz_localize("UTC")
    last_close = last_open + pd.Timedelta(seconds=tf_seconds(tf))
    age = max((now - last_close).total_seconds(), 0.0)
    if tf == "1d":
        limit = 4.5 * 86400       # week-end + jour férié
    elif tf == "1w":
        limit = 10 * 86400
    else:
        limit = max(3 * tf_seconds(tf), 600)
    return {"age_s": age, "stale": age > limit, "last_close": last_close}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoyage : doublons, prix aberrants, cohérence high/low."""
    df = df.drop_duplicates("time").sort_values("time")
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0)]
    df = df.copy()
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    df["volume"] = df["volume"].fillna(0.0)
    return df.reset_index(drop=True)


def _finalize(df: pd.DataFrame, tf: str, asset: "markets.Asset", limit: int,
              closed_only: bool, min_bars: int) -> pd.DataFrame:
    df = _clean(df)
    if closed_only:
        df = drop_forming(df, tf, asset.session)
    df = df.tail(limit).reset_index(drop=True)
    if len(df) < min_bars:
        raise FeedError(f"Pas assez de bougies ({len(df)} < {min_bars}) pour {asset.name} {tf}")
    df["time"] = df["time"].dt.tz_convert(settings.TZ_NAME)
    return df


# ------------------------------------------------------------------
# Routage principal
# ------------------------------------------------------------------
def _plan(a: "markets.Asset") -> list:
    """Liste ordonnée des sources à essayer : (type, symbole, libellé)."""
    plan = []
    suffix = " — même actif que l'OTC" if a.market == "otc" else ""
    if a.binance:
        plan.append(("binance", a.binance, f"Binance ({a.binance}){suffix}"))
    if a.yf:
        tag = ", secours" if a.binance else ""
        plan.append(("yf", a.yf, f"Yahoo Finance ({a.yf}{tag}){suffix}"))
    for sym in a.yf_fallbacks:
        plan.append(("yf", sym, f"Yahoo Finance ({sym}, secours){suffix}"))
    return plan


def fetch_candles(market: str, asset: str, timeframe: str, limit: int = MAX_BARS,
                  closed_only: bool = True, min_bars: int | None = None):
    """
    Renvoie (dataframe, source_label), avec cache court.
    market : 'crypto' | 'forex' | 'stocks' | 'etf' | 'indices' | 'commodities' | 'otc'
    asset  : ex. 'BTC/USDT', 'EUR/USD', 'NVDA', 'US500', 'COPPER'
    timeframe : '1m' '5m' '15m' '1h' '4h' '1d' '1w'
    closed_only : True (défaut) = uniquement des bougies fermées (analyse).
                  False = garde la bougie en cours (pour afficher un prix live).
    """
    timeframe = timeframe.lower()
    if timeframe not in markets.TIMEFRAMES:
        raise FeedError(f"Timeframe inconnu : {timeframe}")
    a = markets.get_asset(market, asset)
    if a is None:
        raise FeedError(f"Actif inconnu : {market}/{asset}")
    if timeframe not in markets.fetchable_timeframes(market):
        raise FeedError(f"Timeframe {timeframe} non disponible pour {asset} "
                        f"(disponibles : {', '.join(markets.timeframes_for(market))})")
    if min_bars is None:
        min_bars = MIN_BARS if closed_only else 2
    limit = max(int(limit), min_bars)

    key = (market, asset, timeframe, limit, closed_only, min_bars)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    naive_tz = markets.SESSIONS[a.session][0] if a.session in markets.SESSIONS else "UTC"
    errors = []
    for kind, sym, label in _plan(a):
        try:
            if kind == "binance":
                raw = fetch_binance(sym, timeframe, limit + 1)
            else:
                raw = fetch_yfinance(sym, timeframe, limit + 1, naive_tz=naive_tz)
            result = (_finalize(raw, timeframe, a, limit, closed_only, min_bars), label)
            _cache_put(key, *result)
            return result[0].copy(), result[1]
        except FeedError as e:
            errors.append(f"{sym} : {e}")
        except Exception as e:     # filet de sécurité : jamais d'exception brute
            log.exception("Erreur inattendue (%s %s)", sym, timeframe)
            errors.append(f"{sym} : {type(e).__name__}")
    raise FeedError(" / ".join(errors) if errors else f"Aucune source pour {asset}")


def fetch_history(market: str, asset: str, timeframe: str, bars: int = 2000):
    """Historique long pour le backtest. Renvoie (df de bougies fermées, source)."""
    timeframe = timeframe.lower()
    a = markets.get_asset(market, asset)
    if a is None:
        raise FeedError(f"Actif inconnu : {market}/{asset}")
    errors = []
    for kind, sym, label in _plan(a):
        try:
            if kind == "binance":
                raw = fetch_binance_history(sym, timeframe, bars + 1)
            else:
                naive_tz = (markets.SESSIONS[a.session][0]
                            if a.session in markets.SESSIONS else "UTC")
                raw = fetch_yfinance(sym, timeframe, bars + 1,
                                     period=markets.YF_MAX_PERIOD.get(timeframe),
                                     naive_tz=naive_tz)
            return _finalize(raw, timeframe, a, bars, True, MIN_BARS), label
        except FeedError as e:
            errors.append(f"{sym} : {e}")
    raise FeedError(" / ".join(errors))


# ------------------------------------------------------------------
# Prix en direct (/prix)
# ------------------------------------------------------------------
def fetch_price(market: str, asset: str) -> dict:
    """Prix courant + variation. Marchés à séances : variation sur la séance
    (clôture précédente) ; autres marchés : variation sur 24 h glissantes."""
    if market in markets.SESSION_MARKETS:
        df, label = fetch_candles(market, asset, "1d", 10, closed_only=False, min_bars=2)
        price = float(df["close"].iloc[-1])
        ref = float(df["close"].iloc[-2])
        span = "sur la séance"
    else:
        df, label = fetch_candles(market, asset, "1h", 60, closed_only=False, min_bars=2)
        price = float(df["close"].iloc[-1])
        target = df["time"].iloc[-1] - pd.Timedelta(hours=24)
        older = df[df["time"] <= target]
        ref = float(older["close"].iloc[-1]) if not older.empty else None
        span = "sur 24h"
    change = (price - ref) / ref * 100 if ref else None
    return {"price": price, "change_pct": change, "span": span, "source": label,
            "time": df["time"].iloc[-1]}


if __name__ == "__main__":
    # Test manuel : python -m data.feed
    t0 = _time.time()
    for mkt, name, tf in [("crypto", "BTC/USDT", "5m"),
                          ("forex", "EUR/USD", "15m"),
                          ("stocks", "NVDA", "1h"),
                          ("indices", "US500", "1d")]:
        try:
            data, src = fetch_candles(mkt, name, tf)
            st = staleness(data, tf)
            print(f"[OK] {mkt:11s} {name:10s} {tf:3s} -> {len(data)} bougies "
                  f"({src}) en {_time.time()-t0:.1f}s "
                  f"| dernière bougie fermée {data['time'].iloc[-1]} "
                  f"{'(MARCHÉ FERMÉ ?)' if st['stale'] else ''}")
        except FeedError as err:
            print(f"[ERREUR] {mkt} {name} {tf} -> {err}")
