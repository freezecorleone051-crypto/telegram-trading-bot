"""Lecture des données du terminal MetaTrader 5 connecté à HFM.

Ce module ne contient volontairement aucune fonction d'envoi d'ordre.
Il utilise uniquement initialize/account_info/symbols_get/symbol_info,
copy_rates_from_pos et symbol_info_tick.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter

import pandas as pd

from config import settings
from data import markets

log = logging.getLogger("tradingbot.mt5")

_SYMBOL_TTL = max(60, int(getattr(settings, "MT5_SYMBOL_REFRESH_MIN", 30)) * 60)
_lock = threading.RLock()
_mt5 = None
_symbol_rows: dict[str, object] = {}
_symbol_cache_at = 0.0


class MT5ProviderError(RuntimeError):
    """Connexion MT5 ou données MT5 indisponibles."""


def _module():
    global _mt5
    if _mt5 is not None:
        return _mt5
    try:
        import MetaTrader5 as mt5
    except (ImportError, OSError) as exc:
        raise MT5ProviderError(
            "Le module MetaTrader5 n'est pas installé. Sur Windows, exécute "
            "python -m pip install --upgrade MetaTrader5 dans un Python Windows "
            "64 bits compatible avec le terminal MetaTrader 5."
        ) from exc
    _mt5 = mt5
    return _mt5


def _ensure_connected():
    """S'attache au terminal déjà ouvert, sans demander ni stocker le mot de passe."""
    mt5 = _module()
    with _lock:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        connected = bool(
            terminal is not None
            and account is not None
            and getattr(terminal, "connected", True)
        )
        if not connected:
            terminal_path = getattr(settings, "MT5_PATH", "").strip()
            try:
                initialized = (mt5.initialize(terminal_path) if terminal_path
                              else mt5.initialize())
            except Exception as exc:
                raise MT5ProviderError(
                    f"Initialisation du terminal MT5 impossible : {exc}. "
                    "Vérifie MT5_PATH, l'installation et l'architecture 64 bits."
                ) from exc
            if not initialized:
                raise MT5ProviderError(
                    f"Connexion au terminal MT5 impossible : {mt5.last_error()}. "
                    "Ouvre MetaTrader 5 et connecte ton compte HFM."
                )
            terminal = mt5.terminal_info()
            account = mt5.account_info()
        if terminal is None or account is None:
            raise MT5ProviderError(
                f"MT5 ne voit aucun compte connecté : {mt5.last_error()}"
            )
        expected_server = getattr(settings, "MT5_SERVER", "").strip()
        actual_server = str(getattr(account, "server", "") or "")
        if expected_server and expected_server.casefold() not in actual_server.casefold():
            raise MT5ProviderError(
                f"Serveur MT5 inattendu : {actual_server or 'inconnu'} ; "
                f"serveur attendu dans MT5_SERVER : {expected_server}."
            )
        return mt5


def _value(row, name, default=None):
    return getattr(row, name, default)


def _normal(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _classify(row, mt5) -> str:
    name = str(_value(row, "name", ""))
    path = str(_value(row, "path", ""))
    description = str(_value(row, "description", ""))
    text = f"{path} {description} {name}".casefold()
    name_up = name.upper()

    # Les groupes/path HFM sont plus fiables que le symbole seul.
    if any(k in text for k in ("crypto", "cryptocurrency", "digital currency")):
        return "crypto"
    normalized_name = _normal(name.lstrip("#"))
    crypto_bases = (
        "BTC", "ETH", "LTC", "XRP", "SOL", "ADA", "BCH", "DOT", "DOGE",
        "AVAX", "LINK", "BNB", "TRX", "ETC", "XLM", "EOS", "NEAR", "ATOM",
        "UNI", "XMR", "SHIB", "FIL", "AAVE",
    )
    crypto_quotes = ("USDT", "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "BTC", "ETH")
    if any(normalized_name.startswith(base + quote)
           for base in crypto_bases for quote in crypto_quotes):
        return "crypto"
    if "etf" in text or "exchange traded fund" in text:
        return "etf"
    if any(k in text for k in ("share", "stock", "equity", "shares")):
        return "stocks"
    if any(k in text for k in ("index", "indices", "indices cash")):
        return "indices"
    if any(k in text for k in (
        "commodity", "commodities", "metal", "gold", "silver", "energy",
        "energies", "oil", "gas", "agriculture", "agricultural", "future",
        "futures", "softs",
    )) or any(k in name_up for k in ("XAU", "XAG", "XPT", "XPD", "XCU", "WTI", "BRENT", "NATGAS")):
        return "commodities"
    if any(k in text for k in ("forex", "fx majors", "fx minors", "currency pair")):
        return "forex"

    calc_mode = _value(row, "trade_calc_mode")
    forex_modes = {
        _value(mt5, "SYMBOL_CALC_MODE_FOREX", 0),
        _value(mt5, "SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE", 5),
    }
    index_modes = {_value(mt5, "SYMBOL_CALC_MODE_CFDINDEX", 3)}
    exchange_stock_modes = {_value(mt5, "SYMBOL_CALC_MODE_EXCH_STOCKS", 32)}
    if calc_mode in exchange_stock_modes:
        return "stocks"
    if calc_mode in index_modes:
        return "indices"
    if calc_mode in forex_modes:
        return "forex"

    # Ne pas perdre silencieusement un instrument inhabituel du broker.
    return "other"


def _build_asset(row, mt5):
    name = str(_value(row, "name", "")).strip()
    if not name:
        return None
    trade_mode = _value(row, "trade_mode")
    disabled_mode = _value(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0)
    if trade_mode is not None and int(trade_mode) == int(disabled_mode):
        return None

    market = _classify(row, mt5)
    description = str(_value(row, "description", "") or "").strip()
    path = str(_value(row, "path", "") or "").strip()
    label = description if description and description.casefold() != name.casefold() else name
    aliases = []
    stripped = name.lstrip("#")
    if stripped != name:
        aliases.append(stripped)
    if description and description != name:
        aliases.append(description)

    try:
        digits = int(_value(row, "digits"))
    except (TypeError, ValueError):
        digits = None
    session = None  # les heures de clôture varient selon le groupe HFM
    return markets.Asset(
        name=name,
        market=market,
        label=label,
        aliases=tuple(aliases),
        decimals=digits,
        session=session,
        hfm_symbol=name,
        path=path,
        trade_mode=int(trade_mode) if trade_mode is not None else None,
    )


def refresh_catalog(force: bool = False) -> list[markets.Asset]:
    """Découvre les symboles réellement exposés par le terminal MT5."""
    global _symbol_rows, _symbol_cache_at
    if not getattr(settings, "MT5_ENABLED", True):
        _symbol_rows = {}
        _symbol_cache_at = 0.0
        markets.register_hfm_assets([])
        return []

    with _lock:
        now = time.monotonic()
        # Vérifie la session même quand le catalogue est encore en cache :
        # un terminal qui vient de se déconnecter ne doit pas sembler actif.
        mt5 = _ensure_connected()
        if _symbol_rows and not force and now - _symbol_cache_at < _SYMBOL_TTL:
            return markets.hfm_assets()
        rows = mt5.symbols_get()
        if rows is None:
            raise MT5ProviderError(f"MT5 n'a pas retourné de symboles : {mt5.last_error()}")
        built = []
        row_map = {}
        seen = set()
        for row in rows:
            asset = _build_asset(row, mt5)
            if asset is None or asset.name in seen:
                continue
            seen.add(asset.name)
            built.append(asset)
            row_map[asset.name] = row
        if not built:
            raise MT5ProviderError(
                "Aucun instrument négociable n'a été retourné par MT5. "
                "Vérifie le compte HFM et les symboles disponibles dans la plateforme."
            )
        _symbol_rows = row_map
        _symbol_cache_at = now
        markets.register_hfm_assets(built)
        counts = Counter(a.market for a in built)
        log.info("Catalogue HFM actualisé : %s instruments (%s)", len(built), dict(counts))
        return built


def _resolve_symbol(market: str, asset: str) -> str | None:
    # Le nom dynamique garde exactement le symbole MT5.
    item = markets.get_asset(market, asset)
    if item and item.hfm_symbol and item.hfm_symbol in _symbol_rows:
        return item.hfm_symbol

    query = _normal(asset)
    if not query:
        return None
    matches = [symbol for symbol in _symbol_rows if _normal(symbol) == query]
    if len(matches) == 1:
        return matches[0]

    # Alias statique (ex. EUR/USD) -> symbole broker (ex. EURUSD).
    if item:
        aliases = [item.name, item.label, *item.aliases]
        for alias in aliases:
            key = _normal(alias)
            matches = [symbol for symbol in _symbol_rows if _normal(symbol) == key]
            if len(matches) == 1:
                return matches[0]
    return None


def has_symbol(market: str, asset: str) -> bool:
    """True si l'actif correspond exactement à un symbole HFM découvert."""
    if not _symbol_rows:
        try:
            refresh_catalog()
        except MT5ProviderError:
            return False
    return _resolve_symbol(market, asset) is not None


def _timeframe_constant(mt5, timeframe: str):
    names = {
        "1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
        "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1",
        "1w": "TIMEFRAME_W1",
    }
    constant = names.get(timeframe)
    value = getattr(mt5, constant, None) if constant else None
    if value is None:
        raise MT5ProviderError(f"Timeframe {timeframe} non pris en charge par MT5")
    return value


def fetch_candles(market: str, asset: str, timeframe: str, limit: int = 500):
    """Renvoie les bougies OHLCV MT5 et l'étiquette de source."""
    with _lock:
        mt5 = _ensure_connected()
        if not _symbol_rows:
            refresh_catalog(force=True)
        symbol = _resolve_symbol(market, asset)
        if not symbol:
            raise MT5ProviderError(f"{asset} n'a pas de symbole HFM MT5 correspondant")

        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5ProviderError(f"Informations MT5 indisponibles pour {symbol}")
        # copy_rates_from_pos lit un symbole du catalogue terminal sans
        # l'ajouter à Market Watch. On évite ainsi d'y afficher plusieurs
        # centaines d'instruments lors du scan universel.
        rates = mt5.copy_rates_from_pos(
            symbol, _timeframe_constant(mt5, timeframe), 1, int(limit)
        )
        if rates is None or len(rates) == 0:
            raise MT5ProviderError(
                f"Aucune bougie MT5 pour {symbol} ({timeframe}) : {mt5.last_error()}"
            )
        frame = pd.DataFrame(rates)
        if "tick_volume" in frame.columns:
            frame["volume"] = frame["tick_volume"]
        elif "real_volume" in frame.columns:
            frame["volume"] = frame["real_volume"]
        if "time" in frame.columns:
            frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        return frame, f"HFM MT5 ({symbol})"


def fetch_tick(market: str, asset: str) -> dict:
    """Dernière cotation du symbole HFM ; pas de fallback vers un autre broker."""
    with _lock:
        mt5 = _ensure_connected()
        if not _symbol_rows:
            refresh_catalog(force=True)
        symbol = _resolve_symbol(market, asset)
        if not symbol:
            raise MT5ProviderError(f"{asset} n'a pas de symbole HFM MT5 correspondant")
        if not mt5.symbol_select(symbol, True):
            raise MT5ProviderError(f"Symbole MT5 {symbol} non sélectionnable")
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5ProviderError(f"Aucun tick MT5 pour {symbol} : {mt5.last_error()}")
        last = float(_value(tick, "last", 0.0) or 0.0)
        bid = float(_value(tick, "bid", 0.0) or 0.0)
        ask = float(_value(tick, "ask", 0.0) or 0.0)
        price = last if last > 0 else ((bid + ask) / 2 if bid > 0 and ask > 0 else bid or ask)
        if price <= 0:
            raise MT5ProviderError(f"Prix MT5 invalide pour {symbol}")
        return {"price": price, "time": _value(tick, "time"), "symbol": symbol}


def connection_status() -> dict:
    """Informations non sensibles sur le terminal connecté."""
    with _lock:
        mt5 = _ensure_connected()
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        symbols = refresh_catalog()
        return {
            "connected": True,
            "server": str(_value(account, "server", "")),
            "company": str(_value(terminal, "company", "")),
            "symbols": len(symbols),
            "categories": dict(Counter(a.market for a in symbols)),
        }
