"""
Indicateurs techniques (calcul pur pandas/numpy, aucune dépendance externe) :
EMA, RSI(14), MACD(12,26,9), ATR(14), Bandes de Bollinger(20,2),
Stochastique(14,3), ADX/DI(14), volume relatif, plus l'arrondi intelligent
des prix.

Tous ces indicateurs sont CAUSAUX : la valeur à la bougie i ne dépend que des
bougies <= i (aucune fuite du futur). C'est vérifié par tests/test_no_lookahead.py.
"""
import numpy as np
import pandas as pd


# ------------------------------------------------------------------
# Moyennes et oscillateurs
# ------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder. Marché parfaitement plat -> 50 (et non 0)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100 - 100 / (1 + rs)
    only_gain = (avg_loss == 0) & (avg_gain > 0)     # aucune perte -> 100
    flat = (avg_loss == 0) & (avg_gain == 0)         # aucun mouvement -> 50
    out = out.mask(only_gain, 100.0).mask(flat, 50.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(close: pd.Series, period: int = 20, nb_dev: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + nb_dev * std
    lower = mid - nb_dev * std
    return mid, upper, lower


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    """Renvoie %K et %D (0-100). Plage nulle (marché plat) -> 50."""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    rng = (high_max - low_min).astype(float)
    flat = (rng == 0) & low_min.notna()
    with np.errstate(divide="ignore", invalid="ignore"):
        k = 100 * (df["close"] - low_min) / rng.where(rng != 0)
    k = k.mask(flat, 50.0)
    d = k.rolling(d_period).mean()
    return k, d


def adx(df: pd.DataFrame, period: int = 14):
    """ADX de Wilder + DI+ / DI-. Mesure la FORCE de la tendance (0-100),
    pas sa direction : > 25 = marché en tendance, < 20 = marché sans direction."""
    high, low = df["high"], df["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df)
    alpha = 1 / period
    atr_w = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
        minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
        denom = (plus_di + minus_di)
        dx = 100 * (plus_di - minus_di).abs() / denom.where(denom != 0)
    adx_s = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return adx_s, plus_di, minus_di


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Volume de la bougie / moyenne des `period` bougies précédentes.
    NaN si le marché ne fournit pas de volume (ex. forex sur Yahoo)."""
    vol = df["volume"].astype(float)
    avg = vol.shift(1).rolling(period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = vol / avg.where(avg > 0)
    return ratio


# ------------------------------------------------------------------
# Séries complètes pour le moteur de signal et les graphiques
# ------------------------------------------------------------------
def compute_series(df: pd.DataFrame) -> dict:
    """Calcule toutes les séries utiles sur le DataFrame complet."""
    close = df["close"]
    macd_line, macd_sig, macd_hist = macd(close)
    bb_mid, bb_up, bb_low = bollinger(close)
    k, d = stochastic(df)
    adx_s, plus_di, minus_di = adx(df)
    atr_s = atr(df, 14)
    return {
        "ema9": ema(close, 9), "ema21": ema(close, 21), "ema50": ema(close, 50),
        "rsi": rsi(close, 14),
        "macd_line": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "bb_mid": bb_mid, "bb_upper": bb_up, "bb_lower": bb_low,
        "stoch_k": k, "stoch_d": d,
        "atr": atr_s,
        "adx": adx_s, "plus_di": plus_di, "minus_di": minus_di,
        "vol_ratio": volume_ratio(df),
    }


# ------------------------------------------------------------------
# Arrondi intelligent du prix selon sa taille
# ------------------------------------------------------------------
def smart_decimals(price: float) -> int:
    """Nombre de décimales adapté à la taille du prix.

    Entre 1 et 10 on garde 5 décimales : c'est la précision standard du forex
    (EUR/USD = 1.09691). Avec 4 décimales, l'arrondi déformait le TP/SL
    jusqu'à ±20 % sur les petits timeframes."""
    p = abs(price)
    if p >= 100:
        return 2
    if p >= 10:
        return 3
    if p >= 0.1:
        return 5
    if p >= 0.01:
        return 6
    return 8


def smart_round(price: float, decimals: int | None = None) -> float:
    return round(price, smart_decimals(price) if decimals is None else decimals)
