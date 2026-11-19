"""
Indicateurs techniques (calcul pur pandas, aucune dépendance externe) :
EMA, RSI(14), MACD(12,26,9), ATR(14), Bandes de Bollinger(20,2),
Stochastique(14,3), plus l'arrondi intelligent des prix.
"""
import pandas as pd


# ------------------------------------------------------------------
# Moyennes et oscillateurs
# ------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # si perte moyenne = 0 -> RSI 100 ; si gain moyen = 0 -> RSI 0
    out = out.where(avg_loss > 0, 100.0)
    out = out.where(avg_gain > 0, out.where(avg_loss > 0, 0.0))
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(close: pd.Series, period: int = 20, nb_dev: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + nb_dev * std
    lower = mid - nb_dev * std
    return mid, upper, lower


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    """Renvoie %K et %D (0-100)."""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    rng = (high_max - low_min).replace(0, pd.NA)
    k = 100 * (df["close"] - low_min) / rng.astype(float)
    d = k.rolling(d_period).mean()
    return k, d


# ------------------------------------------------------------------
# Séries complètes pour le moteur de signal et les graphiques
# ------------------------------------------------------------------
def compute_series(df: pd.DataFrame) -> dict:
    """Calcule toutes les séries utiles sur le DataFrame complet."""
    close = df["close"]
    macd_line, macd_sig, macd_hist = macd(close)
    bb_mid, bb_up, bb_low = bollinger(close)
    k, d = stochastic(df)
    return {
        "ema9": ema(close, 9), "ema21": ema(close, 21),
        "rsi": rsi(close, 14),
        "macd_line": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "bb_mid": bb_mid, "bb_upper": bb_up, "bb_lower": bb_low,
        "stoch_k": k, "stoch_d": d,
        "atr": atr(df, 14),
    }


# ------------------------------------------------------------------
# Arrondi intelligent du prix selon sa taille
# ------------------------------------------------------------------
def smart_round(price: float) -> float:
    p = abs(price)
    if p >= 100:
        d = 2
    elif p >= 10:
        d = 3
    elif p >= 1:
        d = 4
    elif p >= 0.1:
        d = 5
    else:
        d = 6
    return round(price, d)
