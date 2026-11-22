"""
Catalogue des marchés, actifs et sources de cours.

Le bot ne se connecte pas au broker : pour un même actif, le graphique est
le même partout (même marché sous-jacent). data/feed.py essaie Deriv en premier,
puis Binance pour les cryptos et Yahoo Finance comme source de secours.
Les actifs « OTC » sont routés vers l'actif réel correspondant (attention :
voir l'avertissement OTC dans le README).

Pour ajouter un actif, ajoute UNE ligne dans la liste de sa famille plus bas
(voir `_stock(...)`, `_crypto(...)`, etc.) — rien d'autre à modifier.

IMPORTANT (correction du 21/11) : les tables de routage OTC_ROUTE,
FOREX_SYMBOLS, FOREX_FALLBACKS et CRYPTO_YF_FALLBACK en bas de fichier sont
OBLIGATOIRES : data/feed.py les utilise pour récupérer les cours. Elles sont
calculées automatiquement à partir du CATALOGUE — si tu ajoutes un actif,
elles se mettent à jour toutes seules, ne touche à rien d'autre.
"""
import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------
# Timeframes
# ------------------------------------------------------------------
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
              "1d": 86400, "1w": 604800}

# Correspondance vers l'API yfinance : (intervalle, période par défaut).
# Le "4h" est recalculé à partir du "1h".
YF_INTERVAL_MAP = {
    "1m": ("1m", "5d"),
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"),
    "4h": ("60m", "6mo"),
    "1d": ("1d", "2y"),
    "1w": ("1wk", "10y"),
}
# Périodes maximales servies par Yahoo (utilisées par le backtest)
YF_MAX_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "2y",
                 "4h": "2y", "1d": "10y", "1w": "max"}

# Timeframes de contrôle (multi-timeframe) : tf choisi -> [tf, moyen, long]
MTF_MAP = {
    "1m": ["1m", "5m", "15m"],
    "5m": ["5m", "15m", "1h"],
    "15m": ["15m", "1h", "4h"],
    "1h": ["1h", "4h", "1d"],
    "4h": ["4h", "1d"],
    "1d": ["1d", "1w"],
    "1w": ["1w"],
}
# Les marchés à séances (actions...) n'ont pas de vraie bougie 4h : on saute.
MTF_MAP_SESSION = {
    "1m": ["1m", "5m", "15m"],
    "5m": ["5m", "15m", "1h"],
    "15m": ["15m", "1h", "1d"],
    "1h": ["1h", "1d"],
    "1d": ["1d", "1w"],
    "1w": ["1w"],
}

# Séances de bourse : (fuseau, heure de clôture, minute). Sert à savoir si la
# bougie journalière du jour est déjà complète.
SESSIONS = {
    "us": ("America/New_York", 16, 0),
    "fut": ("America/New_York", 17, 0),
    "de": ("Europe/Berlin", 17, 30),
    "fr": ("Europe/Paris", 17, 30),
    "uk": ("Europe/London", 16, 30),
    "jp": ("Asia/Tokyo", 15, 0),
}


@dataclass(frozen=True)
class Asset:
    name: str                 # identifiant tapé / affiché : "NVDA", "BTC/USDT"
    market: str               # crypto | forex | stocks | etf | indices | commodities | otc
    label: str = ""           # nom lisible : "NVIDIA"
    yf: str | None = None     # symbole Yahoo Finance
    binance: str | None = None  # symbole Binance (crypto)
    yf_fallbacks: tuple = ()  # symboles Yahoo de secours
    aliases: tuple = ()       # autres noms reconnus : ("gold", "or")
    decimals: int | None = None  # décimales d'affichage (None = automatique)
    session: str | None = None   # clé de SESSIONS (None = marché 24h)


# ------------------------------------------------------------------
# Fabriques (une ligne par actif)
# ------------------------------------------------------------------
def _crypto(base, label, yf=None):
    return Asset(f"{base}/USDT", "crypto", label, yf=yf or f"{base}-USD",
                 binance=f"{base}USDT", aliases=(base, label))


def _forex(pair, yf=None, label="", fallbacks=(), aliases=()):
    code = pair.replace("/", "")
    return Asset(pair, "forex", label or pair, yf=yf or f"{code}=X",
                 yf_fallbacks=fallbacks, aliases=aliases,
                 decimals=3 if "JPY" in pair else 5)


def _stock(ticker, label, session="us", yf=None):
    return Asset(ticker, "stocks", label, yf=yf or ticker, decimals=2,
                 session=session, aliases=(label,))


def _etf(ticker, label, aliases=()):
    return Asset(ticker, "etf", label, yf=ticker, decimals=2, session="us",
                 aliases=(label,) + tuple(aliases))


def _index(name, label, yf, session, aliases=()):
    return Asset(name, "indices", label, yf=yf, decimals=2, session=session,
                 aliases=(label,) + tuple(aliases))


def _commo(name, label, yf, aliases=(), decimals=2):
    return Asset(name, "commodities", label, yf=yf, decimals=decimals,
                 session="fut", aliases=(label,) + tuple(aliases))


def _otc(name, label, yf, binance=None, decimals=None, fallbacks=()):
    return Asset(name, "otc", label, yf=yf, binance=binance, decimals=decimals,
                 yf_fallbacks=fallbacks)


# ------------------------------------------------------------------
# Catalogue
# ------------------------------------------------------------------
CATALOG = {
    # Crypto : Deriv en priorité, Binance et Yahoo en secours
    "crypto": [
        _crypto("BTC", "Bitcoin"), _crypto("ETH", "Ethereum"),
        _crypto("BNB", "BNB"), _crypto("SOL", "Solana"),
        _crypto("XRP", "XRP"), _crypto("ADA", "Cardano"),
        _crypto("DOGE", "Dogecoin"), _crypto("AVAX", "Avalanche"),
        _crypto("LINK", "Chainlink"), _crypto("DOT", "Polkadot"),
        _crypto("LTC", "Litecoin"), _crypto("TRX", "Tron"),
    ],
    # Forex & Or : Deriv en priorité, Yahoo Finance en secours
    "forex": [
        _forex("EUR/USD"), _forex("GBP/USD"), _forex("USD/JPY"),
        _forex("AUD/USD"), _forex("USD/CAD"), _forex("USD/CHF"),
        _forex("NZD/USD"), _forex("EUR/GBP"), _forex("EUR/JPY"),
        _forex("GBP/JPY"),
        # Yahoo n'a pas XAUUSD=X en intraday : on utilise le contrat COMEX GC=F
        # (même mouvement que l'or spot), avec XAUUSD=X en secours.
        Asset("XAU/USD", "forex", "Or", yf="GC=F", yf_fallbacks=("XAUUSD=X",),
              aliases=("GOLD", "OR", "XAU"), decimals=2, session="fut"),
    ],
    # Actions US : grandes capitalisations liquides (spread faible)
    "stocks": [
        _stock("NVDA", "NVIDIA"), _stock("MSFT", "Microsoft"),
        _stock("AAPL", "Apple"), _stock("GOOGL", "Alphabet"),
        _stock("AMZN", "Amazon"), _stock("META", "Meta"),
        _stock("TSLA", "Tesla"), _stock("AVGO", "Broadcom"),
        _stock("AMD", "AMD"), _stock("TSM", "TSMC"),
        _stock("ASML", "ASML"), _stock("NFLX", "Netflix"),
        _stock("PLTR", "Palantir"), _stock("ORCL", "Oracle"),
        _stock("JPM", "JPMorgan"), _stock("V", "Visa"),
        _stock("LLY", "Eli Lilly"), _stock("COST", "Costco"),
        _stock("WMT", "Walmart"), _stock("BRK-B", "Berkshire Hathaway"),
        # Mines (cuivre / or) : utile pour la région du Katanga
        _stock("FCX", "Freeport-McMoRan"), _stock("SCCO", "Southern Copper"),
        _stock("NEM", "Newmont"),
    ],
    # ETF : un seul produit = tout un panier (moins risqué qu'une action seule)
    "etf": [
        _etf("SPY", "S&P 500 ETF", ("SP500ETF",)),
        _etf("QQQ", "Nasdaq 100 ETF", ("NASDAQETF",)),
        _etf("DIA", "Dow Jones ETF"), _etf("IWM", "Russell 2000 ETF"),
        _etf("SMH", "Semi-conducteurs ETF", ("SEMI", "SEMICONDUCTEURS")),
        _etf("GLD", "Or ETF", ("GOLDETF",)), _etf("TLT", "Obligations US 20+ ans"),
        _etf("XLE", "Énergie ETF"),
    ],
    # Indices boursiers (noms identiques à ceux des courtiers CFD)
    "indices": [
        _index("US500", "S&P 500", "^GSPC", "us", ("SPX", "S&P", "SP500")),
        _index("NAS100", "Nasdaq 100", "^NDX", "us", ("NASDAQ", "NDX", "US100")),
        _index("US30", "Dow Jones", "^DJI", "us", ("DOW", "DJI", "DOWJONES")),
        _index("GER40", "DAX 40", "^GDAXI", "de", ("DAX", "DE40")),
        _index("FRA40", "CAC 40", "^FCHI", "fr", ("CAC", "CAC40")),
        _index("UK100", "FTSE 100", "^FTSE", "uk", ("FTSE",)),
        _index("JPN225", "Nikkei 225", "^N225", "jp", ("NIKKEI", "N225")),
    ],
    # Matières premières (contrats à terme Yahoo)
    "commodities": [
        _commo("XAG/USD", "Argent", "SI=F", ("SILVER", "XAG")),
        _commo("XPT/USD", "Platine", "PL=F", ("PLATINUM", "XPT")),
        _commo("WTI", "Pétrole WTI", "CL=F", ("PETROLE", "OIL", "CRUDE")),
        _commo("BRENT", "Pétrole Brent", "BZ=F"),
        _commo("NATGAS", "Gaz naturel", "NG=F", ("GAZ", "GAS"), decimals=3),
        _commo("COPPER", "Cuivre", "HG=F", ("CUIVRE", "CU"), decimals=3),
    ],
    # OTC / binaires : pas d'API officielle -> cours du MÊME actif réel
    "otc": [
        _otc("EUR/USD OTC", "EUR/USD OTC", "EURUSD=X", decimals=5),
        _otc("GBP/USD OTC", "GBP/USD OTC", "GBPUSD=X", decimals=5),
        _otc("USD/JPY OTC", "USD/JPY OTC", "USDJPY=X", decimals=3),
        _otc("XAU/USD OTC", "XAU/USD OTC", "GC=F", decimals=2,
             fallbacks=("XAUUSD=X",)),
        _otc("BTC/USD OTC", "BTC/USD OTC", "BTC-USD", binance="BTCUSDT"),
        _otc("ETH/USD OTC", "ETH/USD OTC", "ETH-USD", binance="ETHUSDT"),
    ],
}

# Ordre d'affichage / de recherche
MARKET_ORDER = ["crypto", "stocks", "etf", "indices", "commodities", "forex", "otc"]

MARKET_INFO = {
    "crypto": {"label": "Crypto (Deriv, Binance/Yahoo en secours)", "button": "Crypto",
               "timeframes": ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]},
    "stocks": {"label": "Actions US (Deriv / Yahoo Finance)", "button": "Actions",
               "timeframes": ["5m", "15m", "1h", "1d", "1w"]},
    "etf": {"label": "ETF (Deriv / Yahoo Finance)", "button": "ETF",
            "timeframes": ["5m", "15m", "1h", "1d", "1w"]},
    "indices": {"label": "Indices boursiers (Deriv / Yahoo Finance)",
                "button": "Indices", "timeframes": ["5m", "15m", "1h", "1d", "1w"]},
    "commodities": {"label": "Matières premières (Deriv / Yahoo Finance)",
                    "button": "Matières 1res", "timeframes": ["5m", "15m", "1h", "1d", "1w"]},
    "forex": {"label": "Forex & Or (Deriv / Yahoo Finance)", "button": "Forex & Or",
              "timeframes": ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]},
    "otc": {"label": "OTC / Binaires (cours de l'actif réel)", "button": "OTC",
            "timeframes": ["1m", "5m", "15m", "1h"]},
}
# Compatibilité avec l'ancien code
MARKET_LABELS = {m: MARKET_INFO[m]["label"] for m in MARKET_ORDER}
MARKET_EMOJILESS = {m: MARKET_INFO[m]["button"] for m in MARKET_ORDER}

# Marchés à séances d'ouverture (fermés la nuit / le week-end)
SESSION_MARKETS = {"stocks", "etf", "indices", "commodities"}

_BY_KEY = {(a.market, a.name): a for m in MARKET_ORDER for a in CATALOG[m]}

# Rétro-compatibilité : anciennes listes de noms
CRYPTO_ASSETS = [a.name for a in CATALOG["crypto"]]
FOREX_ASSETS = [a.name for a in CATALOG["forex"]]
OTC_ASSETS = [a.name for a in CATALOG["otc"]]

# ------------------------------------------------------------------
# Tables de routage (OBLIGATOIRES pour data/feed.py) — calculées
# automatiquement à partir du CATALOGUE. Ne pas supprimer : sans elles,
# plus aucun cours forex / OTC / secours crypto ne peut être récupéré.
# ------------------------------------------------------------------
# Symbole yfinance par actif forex (ex. "EUR/USD" -> "EURUSD=X")
FOREX_SYMBOLS = {a.name: a.yf for a in CATALOG["forex"] if a.yf}

# Symboles Yahoo de secours par actif forex (ex. XAU/USD -> XAUUSD=X)
FOREX_FALLBACKS = {a.name: list(a.yf_fallbacks)
                   for a in CATALOG["forex"] if a.yf_fallbacks}

# Secours Yahoo par actif crypto (utilisé quand Binance est indisponible)
CRYPTO_YF_FALLBACK = {a.name: a.yf for a in CATALOG["crypto"] if a.yf}

# Routage des actifs OTC vers leur source de cours : ("binance"|"yfinance", symbole)
OTC_ROUTE = {}
for _a in CATALOG["otc"]:
    if _a.binance:
        OTC_ROUTE[_a.name] = ("binance", _a.binance)
    elif _a.yf:
        OTC_ROUTE[_a.name] = ("yfinance", _a.yf)


# ------------------------------------------------------------------
# Accès
# ------------------------------------------------------------------
def get_asset(market: str, name: str) -> Asset | None:
    return _BY_KEY.get((market, name))


def assets_for(market: str) -> list:
    return [a.name for a in CATALOG.get(market, [])]


def timeframes_for(market: str) -> list:
    """Timeframes proposés dans les boutons pour ce marché."""
    return list(MARKET_INFO.get(market, {}).get("timeframes", TIMEFRAMES))


def fetchable_timeframes(market: str) -> list:
    """Timeframes réellement récupérables (plus large que les boutons, pour
    le contrôle multi-timeframe). Pas de bougie 4h propre sur les marchés à
    séances (une séance dure 6h30 : le découpage 4h n'aurait pas de sens)."""
    return [t for t in TIMEFRAMES
            if not (market in SESSION_MARKETS and t == "4h")]


def all_assets() -> list:
    """Renvoie [(market, name), ...] pour tous les actifs connus."""
    return [(m, a.name) for m in MARKET_ORDER for a in CATALOG[m]]


def mtf_map_for(market: str) -> dict:
    return MTF_MAP_SESSION if market in SESSION_MARKETS else MTF_MAP


def decimals_for(market: str, name: str) -> int | None:
    a = get_asset(market, name)
    return a.decimals if a else None


# ------------------------------------------------------------------
# Recherche floue
# ------------------------------------------------------------------
def _tokens(s: str) -> list:
    return re.findall(r"[A-Z0-9]+", s.upper())


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _match_rank(query: str, key: str):
    """Rang de correspondance d'une requête avec un nom (plus petit = mieux).
    Renvoie (rang, jetons_en_trop) ou None."""
    nq, nk = _norm(query), _norm(key)
    if not nq or not nk:
        return None
    if nq == nk:
        return (0, 0)
    tq, tk = _tokens(query), _tokens(key)
    # chaque jeton de la requête doit préfixer un jeton de l'actif, dans l'ordre
    pos = 0
    for t in tq:
        while pos < len(tk) and not tk[pos].startswith(t):
            pos += 1
        if pos == len(tk):
            break
        pos += 1
    else:
        return (1, len(tk) - len(tq))
    if nk.startswith(nq):
        return (2, 0)
    if len(nq) >= 3 and nq in nk:
        return (3, 0)
    return None


def find_asset(query: str):
    """Recherche floue d'un actif. Renvoie (market, asset) ou None.

    'BTC' -> crypto BTC/USDT ; 'nvidia' -> stocks NVDA ; 'EUR' -> forex EUR/USD ;
    'BTC OTC' -> otc BTC/USD OTC ; 'or' -> XAU/USD ; 'dax' -> indices GER40.
    """
    q = (query or "").strip()
    if not q:
        return None
    best = None
    for order, (market, name) in enumerate(all_assets()):
        a = _BY_KEY[(market, name)]
        keys = [a.name, a.label, *a.aliases]
        ranks = [r for r in (_match_rank(q, k) for k in keys if k) if r]
        if not ranks:
            continue
        rank = min(ranks)
        cand = (rank, order, market, name)
        if best is None or cand < best:
            best = cand
    return (best[2], best[3]) if best else None


def infer_market(asset: str) -> str:
    """Devine la famille d'un actif à partir de son nom."""
    for m in MARKET_ORDER:
        if (m, asset) in _BY_KEY:
            return m
    up = asset.upper()
    if up.endswith("OTC"):
        return "otc"
    if up.endswith("/USDT"):
        return "crypto"
    return "forex"


# ------------------------------------------------------------------
# Aide à la saisie (commandes Telegram)
# ------------------------------------------------------------------
DEFAULT_TIMEFRAME = {"crypto": "15m", "forex": "15m", "otc": "5m", "stocks": "1h",
                     "etf": "1h", "indices": "1h", "commodities": "1h"}

TF_ALIASES = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "h1": "1h", "60m": "1h",
              "4h": "4h", "h4": "4h", "1d": "1d", "d": "1d", "d1": "1d", "jour": "1d",
              "1w": "1w", "w": "1w", "w1": "1w", "semaine": "1w"}

MARKET_ALIASES = {
    "crypto": "crypto", "cryptos": "crypto",
    "actions": "stocks", "action": "stocks", "stocks": "stocks", "stock": "stocks",
    "etf": "etf", "etfs": "etf",
    "indices": "indices", "indice": "indices", "index": "indices",
    "matieres": "commodities", "matières": "commodities", "commodities": "commodities",
    "matiere": "commodities", "commo": "commodities",
    "forex": "forex", "fx": "forex", "otc": "otc", "binaire": "otc", "binaires": "otc",
}


def parse_timeframe(token: str) -> str | None:
    """'1H' -> '1h', 'd' -> '1d', 'h4' -> '4h'. None si ce n'est pas un timeframe."""
    return TF_ALIASES.get((token or "").strip().lower())


def parse_market(token: str) -> str | None:
    return MARKET_ALIASES.get((token or "").strip().lower())


def default_timeframe(market: str) -> str:
    return DEFAULT_TIMEFRAME.get(market, "15m")