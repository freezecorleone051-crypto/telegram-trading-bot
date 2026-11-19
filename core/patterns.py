"""
Détection de figures en chandeliers japonais sur les dernières bougies.

Chaque figure renvoie un dictionnaire {name, side, points, text} où side vaut
'buy' (haussier), 'sell' (baissier) ou 'neutral' (indécision, 0 point).
Le moteur de signal limite la contribution des figures à 2 points par côté.

Figures détectées :
- Pin bar / marteau / étoile filante (mèche de rejet 2x le corps)
- Englobante haussière et baissière
- Doji (indécision, 0 point, mentionné quand même)
- Trois soldats blancs / trois corbeaux noirs
- Marubozu (corps plein, momentum fort)
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


def detect_patterns(df: pd.DataFrame, lookback: int = 3) -> list:
    """Détecte les figures sur les `lookback` dernières bougies."""
    found = []
    n = len(df)
    if n < 4:
        return found

    cur = _candle_metrics(df, n - 1)
    prev = _candle_metrics(df, n - 2)
    p2 = _candle_metrics(df, n - 3)

    # --- Pin bar : mèche de rejet >= 2x le corps, corps petit (< 35% de la plage)
    if cur["lower"] >= 2 * cur["body"] and cur["body"] < 0.35 * cur["rng"]:
        found.append({"name": "Pin bar haussière (marteau)", "side": "buy", "points": 1,
                      "text": "longue mèche basse : les vendeurs ont été repoussés"})
    if cur["upper"] >= 2 * cur["body"] and cur["body"] < 0.35 * cur["rng"]:
        found.append({"name": "Pin bar baissière (étoile filante)", "side": "sell",
                      "points": 1,
                      "text": "longue mèche haute : les acheteurs ont été repoussés"})

    # --- Englobante : le corps d'aujourd'hui avale entièrement le corps précédent opposé
    if prev["body"] > BODY_EPS:
        engulfs = (cur["body"] > prev["body"]
                   and max(cur["row"]["open"], cur["row"]["close"]) >= max(prev["row"]["open"], prev["row"]["close"])
                   and min(cur["row"]["open"], cur["row"]["close"]) <= min(prev["row"]["open"], prev["row"]["close"]))
        if engulfs and cur["bull"] and not prev["bull"]:
            found.append({"name": "Englobante haussière", "side": "buy", "points": 1,
                          "text": "la bougie verte avale la bougie rouge précédente"})
        elif engulfs and not cur["bull"] and prev["bull"]:
            found.append({"name": "Englobante baissière", "side": "sell", "points": 1,
                          "text": "la bougie rouge avale la bougie verte précédente"})

    # --- Doji : corps <= 10% de la plage -> indécision (0 point)
    if cur["body"] <= 0.10 * cur["rng"] and cur["rng"] > 0:
        found.append({"name": "Doji", "side": "neutral", "points": 0,
                      "text": "corps minuscule : indécision du marché, prudence"})

    # --- Marubozu : corps >= 80% de la plage -> momentum
    if cur["body"] >= 0.80 * cur["rng"]:
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
        return "Aucune figure de chandelier notable sur les dernières bougies."
    parts = [f"{f['name']} ({f['text']})" for f in found]
    return "Figures détectées : " + " ; ".join(parts)
