
"""
MT5 Feed - HFM Trading Bot
Gestion de la connexion MetaTrader 5 et récupération
des données de marché.
"""

import logging
import pandas as pd

logger = logging.getLogger(__name__)
_mt5 = None


def _api():
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as mt5
        except (ImportError, OSError) as exc:
            logger.error("API MetaTrader5 indisponible : %s", exc)
            return None
        _mt5 = mt5
    return _mt5


class MT5Feed:
    def __init__(self):
        self.connected = False

    def connect(self, login=None, password=None, server=None, path=None):
        """Initialise la connexion à MetaTrader 5."""

        mt5 = _api()
        if mt5 is None:
            self.connected = False
            return False

        kwargs = {}

        if path:
            kwargs["path"] = path

        if login is not None:
            kwargs["login"] = int(login)

        if password:
            kwargs["password"] = password

        if server:
            kwargs["server"] = server

        self.connected = mt5.initialize(**kwargs)

        if not self.connected:
            logger.error(
                "Échec connexion MT5 : %s",
                mt5.last_error()
            )
            return False

        logger.info("Connexion MT5 établie.")
        return True

    def disconnect(self):
        """Ferme la connexion MT5."""

        if self.connected:
            mt5 = _api()
            if mt5 is None:
                self.connected = False
                return
            mt5.shutdown()
            self.connected = False
            logger.info("Connexion MT5 fermée.")

    def get_tick(self, symbol):
        """Récupère le dernier prix du symbole."""

        mt5 = _api()
        if not self.connected or mt5 is None:
            logger.error("MT5 n'est pas connecté.")
            return None

        if not mt5.symbol_select(symbol, True):
            logger.error("Symbole indisponible : %s", symbol)
            return None

        tick = mt5.symbol_info_tick(symbol)

        if tick is None:
            logger.error("Impossible de récupérer le tick : %s", symbol)
            return None

        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "time": tick.time
        }

    def get_rates(self, symbol, timeframe=None, count=100):
        """Récupère les chandeliers OHLCV."""

        mt5 = _api()
        if not self.connected or mt5 is None:
            logger.error("MT5 n'est pas connecté.")
            return None
        if timeframe is None:
            timeframe = mt5.TIMEFRAME_M5

        if not mt5.symbol_select(symbol, True):
            logger.error("Symbole indisponible : %s", symbol)
            return None

        rates = mt5.copy_rates_from_pos(
            symbol,
            timeframe,
            0,
            count
        )

        if rates is None or len(rates) == 0:
            logger.error("Aucune donnée disponible pour %s", symbol)
            return None

        df = pd.DataFrame(rates)

        df["time"] = pd.to_datetime(
            df["time"],
            unit="s",
            utc=True
        )

        return df

    def get_symbols(self):
        """Retourne les symboles disponibles."""

        mt5 = _api()
        if not self.connected or mt5 is None:
            return []

        symbols = mt5.symbols_get()

        if symbols is None:
            return []

        return [symbol.name for symbol in symbols]

    def is_connected(self):
        """Vérifie l'état de la connexion."""

        mt5 = _api()
        return bool(self.connected and mt5 is not None and mt5.terminal_info() is not None)
