"""
Historique des signaux et suivi automatique des résultats.

Chaque signal confirmé (direction != ATTENDRE) est enregistré dans
data_store/signals.json. Un contrôle périodique vérifie sur les bougies
réelles si le Take Profit ou le Stop Loss a été touché en premier :
  - TP touché en premier  -> statut "win"
  - SL touché en premier  -> statut "loss"
  - les deux dans la même bougie -> "loss" (hypothèse conservatrice)
Le fichier data_store/watchlist.json garde la watchlist personnalisée.
"""
import json
import threading
from datetime import datetime, timezone

from config import settings

_lock = threading.Lock()
SIGNALS_FILE = settings.DATA_DIR / "signals.json"
WATCHLIST_FILE = settings.DATA_DIR / "watchlist.json"


# ------------------------------------------------------------------
# Persistance JSON générique
# ------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)


# ------------------------------------------------------------------
# Signaux
# ------------------------------------------------------------------
def add_signal(signal: dict, market: str, asset: str, timeframe: str,
               source: str) -> bool:
    """Enregistre un signal confirmé. Renvoie False si un signal identique
    (même actif/timeframe/direction) est déjà ouvert."""
    with _lock:
        data = _load(SIGNALS_FILE, {"signals": []})
        sigs = data.get("signals", [])
        for s in sigs:
            if (s.get("status") == "open" and s.get("asset") == asset
                    and s.get("tf") == timeframe
                    and s.get("direction") == signal["direction"]):
                return False
        sigs.append({
            "id": len(sigs) + 1,
            "time": datetime.now(timezone.utc).isoformat(),
            "market": market, "asset": asset, "tf": timeframe,
            "direction": signal["direction"], "confidence": signal["confidence"],
            "entry": signal["entry"], "tp": signal["tp"], "sl": signal["sl"],
            "source": source, "status": "open",
        })
        # garde-fou taille : on conserve les 500 derniers
        data["signals"] = sigs[-500:]
        _save(SIGNALS_FILE, data)
        return True


def open_signals() -> list:
    return [s for s in _load(SIGNALS_FILE, {"signals": []}).get("signals", [])
            if s.get("status") == "open"]


def update_open_signals(fetch_candles_fn) -> int:
    """Vérifie les signaux ouverts sur les bougies récentes.

    fetch_candles_fn(market, asset, tf) -> (df, source).
    Renvoie le nombre de signaux clôturés.
    """
    closed = 0
    with _lock:
        data = _load(SIGNALS_FILE, {"signals": []})
        sigs = data.get("signals", [])
        changed = False
        # groupe les signaux ouverts par marché/actif/tf pour limiter les requêtes
        groups = {}
        for s in sigs:
            if s.get("status") == "open":
                groups.setdefault((s["market"], s["asset"], s["tf"]), []).append(s)
        for (mkt, asset, tf), group in groups.items():
            try:
                df, _ = fetch_candles_fn(mkt, asset, tf, 100)
            except Exception:
                continue
            for s in group:
                try:
                    t0 = datetime.fromisoformat(s["time"])
                except ValueError:
                    continue
                after = df[df["time"] > pd_time(t0)]
                if after.empty:
                    continue
                status = None
                for _, row in after.iterrows():
                    hit_tp = row["high"] >= s["tp"] if s["direction"] == "ACHAT" \
                        else row["low"] <= s["tp"]
                    hit_sl = row["low"] <= s["sl"] if s["direction"] == "ACHAT" \
                        else row["high"] >= s["sl"]
                    if hit_sl:          # les deux -> conservateur : perte
                        status = "loss"
                        break
                    if hit_tp:
                        status = "win"
                        break
                if status:
                    s["status"] = status
                    s["closed_time"] = datetime.now(timezone.utc).isoformat()
                    closed += 1
                    changed = True
        if changed:
            _save(SIGNALS_FILE, data)
    return closed


def pd_time(dt: datetime):
    """Convertit un datetime python en timestamp comparable aux colonnes time."""
    import pandas as pd
    return pd.Timestamp(dt)


def stats() -> dict:
    sigs = _load(SIGNALS_FILE, {"signals": []}).get("signals", [])
    wins = [s for s in sigs if s.get("status") == "win"]
    losses = [s for s in sigs if s.get("status") == "loss"]
    per_asset = {}
    for s in wins + losses:
        a = per_asset.setdefault(s["asset"], {"win": 0, "loss": 0})
        a[s["status"]] += 1
    return {"total": len(sigs), "open": len([s for s in sigs if s.get("status") == "open"]),
            "win": len(wins), "loss": len(losses), "per_asset": per_asset}


# ------------------------------------------------------------------
# Watchlist persistante
# ------------------------------------------------------------------
def get_watchlist() -> list:
    data = _load(WATCHLIST_FILE, None)
    if data and isinstance(data.get("items"), list) and data["items"]:
        return [(m, a, t) for m, a, t in data["items"]]
    return list(settings.WATCHLIST)


def watchlist_add(market: str, asset: str, tf: str) -> bool:
    with _lock:
        items = get_watchlist()
        if any(a == asset and t == tf for _m, a, t in items):
            return False
        items.append((market, asset, tf))
        _save(WATCHLIST_FILE, {"items": items})
        return True


def watchlist_remove(market: str, asset: str, tf: str) -> bool:
    with _lock:
        items = get_watchlist()
        new = [(m, a, t) for m, a, t in items if not (a == asset and t == tf)]
        if len(new) == len(items):
            return False
        _save(WATCHLIST_FILE, {"items": new})
        return True


def watchlist_clear():
    with _lock:
        _save(WATCHLIST_FILE, {"items": []})
