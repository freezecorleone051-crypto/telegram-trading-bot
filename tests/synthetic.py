"""Générateur de bougies SIMULÉES pour les tests (aucun réseau).

Chaque bougie est construite à partir de sous-pas aléatoires, ce qui donne des
mèches et des corps réalistes. Les séries ne contiennent AUCUN avantage
exploitable en dehors de ce que l'on injecte :
  - `drift`     : tendance (rendement moyen par bougie) ;
  - `reversion` : force de rappel vers le prix de départ du segment (range).
Cela permet de vérifier que le moteur ne « voit » pas de signal magique dans
le hasard pur, et qu'il se comporte comme prévu dans chaque régime.
"""
import numpy as np
import pandas as pd


def make_ohlc(n=600, seed=1, drift=0.0, vol=0.002, start=100.0, freq="15min",
              segments=None, volume=True, substeps=12, reversion=0.0):
    """Renvoie un DataFrame time/open/high/low/close/volume.

    drift, vol : rendement moyen et écart-type PAR BOUGIE (ex. 0.002 = 0,2 %).
    reversion  : rappel vers le prix d'ancrage (0 = marche aléatoire pure).
    segments   : liste de (nb_bougies, drift, vol[, reversion]) pour enchaîner
                 des régimes ; remplace n/drift/vol/reversion.
    """
    rng = np.random.default_rng(seed)
    if segments is None:
        segments = [(n, drift, vol, reversion)]
    rows = []
    price = float(start)
    for seg in segments:
        length, d, v = seg[0], seg[1], seg[2]
        kappa = seg[3] if len(seg) > 3 else 0.0
        anchor = np.log(price)
        for _ in range(length):
            o = price
            lo = hi = price
            noise = rng.normal(0.0, v / np.sqrt(substeps), substeps)
            for k in range(substeps):
                pull = kappa / substeps * (anchor - np.log(price))
                price *= float(np.exp(d / substeps + pull + noise[k]))
                lo, hi = min(lo, price), max(hi, price)
            c = price
            vol_bar = float(rng.lognormal(mean=np.log(1000), sigma=0.4)
                            * (1 + 40 * abs(np.log(c / o)))) if volume else 0.0
            rows.append((o, hi, lo, c, vol_bar))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "time", pd.date_range("2025-01-01", periods=len(df), freq=freq, tz="UTC"))
    return df
