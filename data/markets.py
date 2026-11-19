"""
Catalogue des marchés, actifs et sources de cours.

Le bot ne se connecte pas au broker : pour un même actif, le graphique est
le même partout (même marché sous-jacent). Crypto via l'API publique Binance,
forex via Yahoo Finance, et les actifs OTC sont routés vers le même actif
ailleurs.
"""

# Timeframes proposés dans les boutons
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Correspondance vers l'API yfinance (le "4h" est recalculé à partir du "1h")
YF_INTERVAL_MAP = {
    "1m": ("1m", "5d"),
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"),
    "4h": ("60m", "6mo"),
}

# ------------------------------------------------------------------
# Actifs par famille de marché
# ------------------------------------------------------------------
# crypto : cours en direct via l'API publique Binance (aucune clé nécessaire)
CRYPTO_ASSETS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT",
                 "XRP/USDT", "ADA/USDT", "DOGE/USDT"]

# forex : cours via Yahoo Finance, mêmes cours que ton courtier
FOREX_ASSETS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
                "USD/CAD", "USD/CHF", "NZD/USD", "XAU/USD"]

# OTC / binaires (Quotex, Pocket Option...) : pas d'API officielle.
# Le bot utilise le cours du MÊME actif sur Binance ou Yahoo -> graphique identique.
OTC_ASSETS = ["EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC",
              "XAU/USD OTC", "BTC/USD OTC", "ETH/USD OTC"]

# Routage des actifs OTC vers leur source de cours (source, symbole)
OTC_ROUTE = {
    "EUR/USD OTC": ("yfinance", "EURUSD=X"),
    "GBP/USD OTC": ("yfinance", "GBPUSD=X"),
    "USD/JPY OTC": ("yfinance", "USDJPY=X"),
    # Pour l'or : Yahoo n'a pas XAUUSD=X, on utilise le contrat COMEX GC=F
    # (même mouvement que l'or spot), avec XAUUSD=X en secours.
    "XAU/USD OTC": ("yfinance", "GC=F"),
    "BTC/USD OTC": ("binance", "BTCUSDT"),
    "ETH/USD OTC": ("binance", "ETHUSDT"),
}

# Symbole yfinance de secours si Binance est indisponible (blocage réseau, etc.)
CRYPTO_YF_FALLBACK = {
    "BTC/USDT": "BTC-USD", "ETH/USDT": "ETH-USD", "BNB/USDT": "BNB-USD",
    "SOL/USDT": "SOL-USD", "XRP/USDT": "XRP-USD", "ADA/USDT": "ADA-USD",
    "DOGE/USDT": "DOGE-USD",
}

# Symboles Yahoo Finance pour le forex + secours éventuels
FOREX_SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "NZD/USD": "NZDUSD=X", "XAU/USD": "GC=F",
}
FOREX_FALLBACKS = {"XAU/USD": ["XAUUSD=X"]}

MARKET_LABELS = {
    "crypto": "Crypto (source : Binance)",
    "forex": "Forex & Or (source : Yahoo Finance)",
    "otc": "OTC / Binaires (cours du même actif ailleurs)",
}

MARKET_EMOJILESS = {"crypto": "Crypto", "forex": "Forex & Or", "otc": "OTC"}


def assets_for(market: str) -> list:
    return {"crypto": CRYPTO_ASSETS, "forex": FOREX_ASSETS,
            "otc": OTC_ASSETS}.get(market, [])


def all_assets():
    """Renvoie [(market, asset), ...] pour tous les actifs connus."""
    return ([("crypto", a) for a in CRYPTO_ASSETS] +
            [("forex", a) for a in FOREX_ASSETS] +
            [("otc", a) for a in OTC_ASSETS])


def find_asset(query: str):
    """Recherche floue d'un actif. Renvoie (market, asset) ou None.

    'BTC' -> crypto BTC/USDT ; 'EUR' -> forex EUR/USD ; 'BTC OTC' -> otc...
    """
    q = query.upper().strip()
    if not q:
        return None
    all_ = all_assets()
    exact = [(m, a) for m, a in all_ if a.upper() == q]
    start = [(m, a) for m, a in all_ if a.upper().startswith(q)]
    contains = [(m, a) for m, a in all_ if q in a.upper()]
    match = exact or start or contains
    return match[0] if match else None


def infer_market(asset: str) -> str:
    """Devine la famille d'un actif à partir de son nom."""
    if asset.upper().endswith("OTC"):
        return "otc"
    if asset.upper() in CRYPTO_ASSETS or asset.upper().endswith("/USDT"):
        return "crypto"
    return "forex"
