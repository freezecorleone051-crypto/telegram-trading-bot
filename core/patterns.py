"""
Détection de figures en chandeliers japonais sur la dernière bougie FERMÉE.

Chaque figure renvoie un dictionnaire {name, side, points, text} où side vaut
'buy' (haussier), 'sell' (baissier) ou 'neutral' (indécision, 0 point).
Le moteur de signal limite la contribution des figures à 2 points par côté.

Figures détectées :
- Pin bar / marteau / étoile filante (grande mèche de rejet, petit corps)
- Englobante haussière et baissière
- Doji (indécision, 0 point, mentionné quand même)
- Trois soldats blancs / trois corbeaux noirs
- Marubozu (corps plein, momentum fort)

Filtres de qualité (corrections par rapport à la v1) :
- une pin bar exige une mèche d'au moins 60 % de la bougie ET 2x le corps, avec
  la mèche opposée courte (l'ancienne règle se déclenchait sur tout doji à corps nul) ;
- si l'ATR est fourni, les englobantes et marubozu trop petits (bruit) sont ignorés ;
- une figure de retournement (pin bar) n'a de valeur qu'à un extrême : après une
  baisse pour un marteau, après une hausse pour une étoile filante. Sinon elle
  est signalée mais ne rapporte aucun point.
"""
import pandas as pd

BODY_EPS = 1e-12


def _candle_metrics(df: pd.DataFrame, i: int) -> dict:
    row = df.iloc[i]
    rng = max(row["high"] - row["low"], BODY_EPS)
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]
    return {"row": row, "rng": rng, "body": body,
            "upper": upper_wick, "lower": lower_wick,
            "bull": row["close"] >= row["open"]}


def _prior_move(df: pd.DataFrame, bars: int = 4):
    """Sens du mouvement qui précède la dernière bougie : 'down' / 'up' / None."""
    if len(df) < bars + 2:
        return None
    ref = float(df["close"].iloc[-2 - bars])
    last = float(df["close"].iloc[-2])
    if last < ref:
        return "down"
    if last > ref:
        return "up"
    return None


def detect_patterns(df: pd.DataFrame, lookback: int = 3,
                    atr_val: float | None = None) -> list:
    """Détecte les figures sur la dernière bougie (et les 2 précédentes pour
    les figures à 3 bougies). `atr_val` active le filtre de taille."""
    found = []
    n = len(df)
    if n < 4:
        return found

    cur = _candle_metrics(df, n - 1)
    prev = _candle_metrics(df, n - 2)
    prior = _prior_move(df)
    big_enough = (atr_val is None) or cur["rng"] >= 0.6 * atr_val

    is_doji = cur["body"] <= 0.10 * cur["rng"]

    # --- Pin bar : mèche de rejet >= 60 % de la bougie et >= 2x le corps,
    #     mèche opposée courte (<= 25 %)
    long_low = (cur["lower"] >= 0.60 * cur["rng"] and cur["lower"] >= 2 * cur["body"]
                and cur["upper"] <= 0.25 * cur["rng"])
    long_up = (cur["upper"] >= 0.60 * cur["rng"] and cur["upper"] >= 2 * cur["body"]
               and cur["lower"] <= 0.25 * cur["rng"])
    if long_low and big_enough:
        ok = prior in (None, "down")
        found.append({
            "name": "Pin bar haussière (marteau)", "side": "buy",
            "points": 1 if ok else 0,
            "text": "longue mèche basse : les vendeurs ont été repoussés"
                    + ("" if ok else " (mais après une hausse : peu fiable, 0 point)")})
    if long_up and big_enough:
        ok = prior in (None, "up")
        found.append({
            "name": "Pin bar baissière (étoile filante)", "side": "sell",
            "points": 1 if ok else 0,
            "text": "longue mèche haute : les acheteurs ont été repoussés"
                    + ("" if ok else " (mais après une baisse : peu fiable, 0 point)")})

    # --- Englobante : le corps actuel avale entièrement le corps précédent opposé
    if prev["body"] > BODY_EPS and big_enough:
        cur_hi = max(cur["row"]["open"], cur["row"]["close"])
        cur_lo = min(cur["row"]["open"], cur["row"]["close"])
        prev_hi = max(prev["row"]["open"], prev["row"]["close"])
        prev_lo = min(prev["row"]["open"], prev["row"]["close"])
        engulfs = cur["body"] > prev["body"] and cur_hi >= prev_hi and cur_lo <= prev_lo
        if engulfs and cur["bull"] and not prev["bull"]:
            found.append({"name": "Englobante haussière", "side": "buy", "points": 1,
                          "text": "la bougie verte avale la bougie rouge précédente"})
        elif engulfs and not cur["bull"] and prev["bull"]:
            found.append({"name": "Englobante baissière", "side": "sell", "points": 1,
                          "text": "la bougie rouge avale la bougie verte précédente"})

    # --- Doji : corps <= 10% de la plage -> indécision (0 point)
    if is_doji:
        found.append({"name": "Doji", "side": "neutral", "points": 0,
                      "text": "corps minuscule : indécision du marché, prudence"})

    # --- Marubozu : corps >= 80% de la plage -> momentum
    if cur["body"] >= 0.80 * cur["rng"] and (atr_val is None or cur["rng"] >= 0.8 * atr_val):
        side = "buy" if cur["bull"] else "sell"
        found.append({"name": "Marubozu " + ("haussier" if cur["bull"] else "baissier"),
                      "side": side, "points": 1,
                      "text": "bougie corps plein : momentum fort "
                              + ("acheteur" if cur["bull"] else "vendeur")})

    # --- Trois soldats blancs / trois corbeaux noirs
    c = df["close"].iloc[-3:]
    o = df["open"].iloc[-3:]
    if all(c.iloc[k] > c.iloc[k - 1] for k in (1, 2)) and all(c.iloc[k] > o.iloc[k] for k in range(3)):
        found.append({"name": "Trois soldats blancs", "side": "buy", "points": 1,
                      "text": "3 bougies vertes consécutives en hausse"})
    if all(c.iloc[k] < c.iloc[k - 1] for k in (1, 2)) and all(c.iloc[k] < o.iloc[k] for k in range(3)):
        found.append({"name": "Trois corbeaux noirs", "side": "sell", "points": 1,
                      "text": "3 bougies rouges consécutives en baisse"})

    # Déduplication par nom (une figure = un seul message)
    seen, unique = set(), []
    for f in found:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)
    return unique


def summarize(found: list) -> str:
    """Résume les figures détectées en une ligne de texte."""
    if not found:
        return "Aucune figure de chandelier notable sur la dernière bougie."
    parts = [f"{f['name']} ({f['text']})" for f in found]
    return "Figures détectées : " + " ; ".join(parts)
