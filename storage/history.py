"""
Historique des signaux et suivi automatique des résultats.

Chaque signal confirmé (direction != ATTENDRE) est enregistré dans
data_store/signals.json. Un contrôle périodique vérifie sur les bougies
FERMÉES suivantes si le Take Profit ou le Stop Loss est touché en premier :
  - TP touché en premier               -> "win"   (+RR en multiples de R)
  - SL touché en premier               -> "loss"  (-1 R)
  - les deux dans la même bougie       -> "loss"  (hypothèse conservatrice)
  - ni l'un ni l'autre après N bougies -> "expired" (sortie à la clôture,
                                          résultat réel en R, positif ou négatif)
  - historique de cours trop court pour trancher -> "unknown" (exclu des stats)

Corrections par rapport à la v1 :
  - écriture ATOMIQUE (fichier temporaire + remplacement) : une coupure pendant
    la sauvegarde ne corrompt plus le fichier ;
  - un fichier illisible est MIS DE CÔTÉ (signals.json.corrupt-...) au lieu
    d'être écrasé en silence par un historique vide ;
  - les identifiants ne se répètent plus après la purge des anciens signaux ;
  - plus de signal « ouvert pour toujours » : expiration après N bougies ;
  - les requêtes réseau se font HORS du verrou (le bot ne se fige plus) ;
  - statistiques en multiples de R (espérance, facteur de profit), pas
    seulement un taux de réussite trompeur.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

import pandas as pd

from config import settings
from data import markets
from utils.helpers import wilson_interval

log = logging.getLogger("tradingbot.history")

_lock = threading.RLock()
SIGNALS_FILE = settings.DATA_DIR / "signals.json"
WATCHLIST_FILE = settings.DATA_DIR / "watchlist.json"

# Nombre de bougies après lesquelles un signal sans TP/SL est clôturé
EXPIRY_BARS = {"1m": 60, "5m": 48, "15m": 32, "1h": 36, "4h": 24, "1d": 15, "1w": 8}
KEEP_CLOSED = 1000


# ------------------------------------------------------------------
# Persistance JSON robuste
# ------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # On ne détruit JAMAIS un fichier illisible : on le met de côté.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, backup)
            log.error("%s illisible (%s) : conservé sous %s, nouveau fichier créé",
                      path.name, e, backup.name)
        except OSError:
            log.error("%s illisible (%s) et impossible à mettre de côté", path.name, e)
        return default


def _save(path, data):
    """Écriture atomique : fichier temporaire, fsync, puis remplacement."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_signals() -> dict:
    data = _load(SIGNALS_FILE, {"signals": [], "next_id": 1})
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        data = {"signals": [], "next_id": 1}
    ids = [s.get("id", 0) for s in data["signals"] if isinstance(s.get("id"), int)]
    data["next_id"] = max(int(data.get("next_id", 1)), (max(ids) + 1) if ids else 1)
    return data


def _trim(sigs: list) -> list:
    """Garde tous les signaux ouverts + les KEEP_CLOSED plus récents clôturés."""
    open_ = [s for s in sigs if s.get("status") == "open"]
    closed = [s for s in sigs if s.get("status") != "open"][-KEEP_CLOSED:]
    # Filtre par identité Python-objet (sûr ici car on opère sur la MÊME liste en mémoire)
    keep_ids = {id(s) for s in open_ + closed}
    return [s for s in sigs if id(s) in keep_ids]


# ------------------------------------------------------------------
# Signaux
# ------------------------------------------------------------------
def add_signal(signal: dict, market: str, asset: str, timeframe: str,
               source: str, origin: str = "manual") -> bool:
    """Enregistre un signal confirmé. Renvoie False (rien d'enregistré) si :
    - un signal identique (même actif/timeframe/direction) est déjà ouvert ;
    - ce signal a déjà été enregistré pour la MÊME bougie (évite de le
      renvoyer à chaque scan tant que la bougie fermée est la même)."""
    if signal.get("direction") not in ("ACHAT", "VENTE"):
        return False
    bar_time = signal.get("bar_time")
    with _lock:
        data = _load_signals()
        sigs = data["signals"]
        for s in sigs:
            if (s.get("asset") == asset and s.get("tf") == timeframe
                    and s.get("direction") == signal["direction"]):
                if s.get("status") == "open":
                    return False
                if bar_time and s.get("bar_time") == bar_time:
                    return False
        sigs.append({
            "id": data["next_id"],
            "time": datetime.now(timezone.utc).isoformat(),
            "bar_time": bar_time,
            "market": market, "asset": asset, "tf": timeframe,
            "direction": signal["direction"],
            "confidence": signal.get("confidence"), "score": signal.get("score"),
            "regime": signal.get("regime"),
            "entry": signal["entry"], "tp": signal["tp"], "sl": signal["sl"],
            "rr": signal.get("rr"),
            "source": source, "origin": origin, "status": "open",
        })
        data["next_id"] += 1
        data["signals"] = _trim(sigs)
        _save(SIGNALS_FILE, data)
        return True


def open_signals() -> list:
    return [s for s in _load_signals()["signals"] if s.get("status") == "open"]


def evaluate_signal(sig: dict, df: pd.DataFrame, timeframe: str):
    """Évalue UN signal sur des bougies FERMÉES (fonction pure, testable).

    Renvoie None si le signal reste ouvert, sinon un dict
    {status, r, exit_time, exit_price}."""
    tf_s = markets.TF_SECONDS.get(timeframe, 300)
    ref = sig.get("bar_time") or sig.get("time")
    try:
        t0 = pd.Timestamp(ref)
    except (ValueError, TypeError):
        return None
    if t0.tzinfo is None:
        t0 = t0.tz_localize("UTC")
    if df.empty:
        return None
    # l'historique disponible doit remonter jusqu'à la bougie du signal,
    # sinon on pourrait rater un TP/SL touché dans l'intervalle manquant
    first = pd.Timestamp(df["time"].iloc[0])
    if first > t0 + pd.Timedelta(seconds=tf_s * 1.5):
        return {"status": "unknown", "r": 0.0, "exit_time": None, "exit_price": None}

    after = df[df["time"] > t0]
    if after.empty:
        return None
    sign = 1 if sig["direction"] == "ACHAT" else -1
    entry, tp, sl = float(sig["entry"]), float(sig["tp"]), float(sig["sl"])
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    reward_r = abs(tp - entry) / risk
    max_bars = EXPIRY_BARS.get(timeframe, 40)
    for k, (_, row) in enumerate(after.iterrows(), start=1):
        hit_sl = row["low"] <= sl if sign > 0 else row["high"] >= sl
        hit_tp = row["high"] >= tp if sign > 0 else row["low"] <= tp
        if hit_sl:          # SL prioritaire si les deux : hypothèse conservatrice
            return {"status": "loss", "r": -1.0, "exit_time": str(row["time"]),
                    "exit_price": sl}
        if hit_tp:
            return {"status": "win", "r": reward_r, "exit_time": str(row["time"]),
                    "exit_price": tp}
        if k >= max_bars:   # trop long : sortie à la clôture de cette bougie
            px = float(row["close"])
            return {"status": "expired", "r": sign * (px - entry) / risk,
                    "exit_time": str(row["time"]), "exit_price": px}
    return None


def update_open_signals(fetch_candles_fn) -> list:
    """Vérifie les signaux ouverts sur les cours récents.

    fetch_candles_fn(market, asset, tf, limit) -> (df de bougies fermées, source).
    Renvoie la liste des signaux qui viennent d'être clôturés (dicts complets).
    Les requêtes réseau se font hors du verrou.
    """
    with _lock:
        groups = {}
        for s in _load_signals()["signals"]:
            if s.get("status") == "open":
                groups.setdefault((s["market"], s["asset"], s["tf"]), []).append(s["id"])
    if not groups:
        return []

    results = {}                                  # id -> résultat d'évaluation
    for (mkt, asset, tf), ids in groups.items():
        try:
            df, _ = fetch_candles_fn(mkt, asset, tf, 250)
        except Exception as e:
            log.warning("Suivi %s %s : cours indisponibles (%s)", asset, tf, e)
            continue
        with _lock:
            by_id = {s["id"]: s for s in _load_signals()["signals"]}
        for sid in ids:
            s = by_id.get(sid)
            if not s:
                continue
            try:
                res = evaluate_signal(s, df, tf)
            except Exception as e:                 # un signal bizarre ne bloque pas les autres
                log.warning("Évaluation du signal %s impossible : %s", sid, e)
                continue
            if res:
                results[sid] = res

    if not results:
        return []
    newly = []
    with _lock:
        data = _load_signals()          # rechargé : d'autres threads ont pu écrire
        now = datetime.now(timezone.utc).isoformat()
        for s in data["signals"]:
            res = results.get(s.get("id"))
            if res and s.get("status") == "open":
                s.update({"status": res["status"], "r": round(res["r"], 3),
                          "closed_time": now, "exit_time": res["exit_time"],
                          "exit_price": res["exit_price"]})
                newly.append(dict(s))
        _save(SIGNALS_FILE, data)
    return newly


def _r_of(s: dict) -> float:
    """R réalisé d'un signal clôturé (compatible avec les anciens fichiers v1)."""
    if isinstance(s.get("r"), (int, float)):
        return float(s["r"])
    if s.get("status") == "loss":
        return -1.0
    if s.get("status") == "win":
        try:
            return abs(s["tp"] - s["entry"]) / abs(s["entry"] - s["sl"])
        except (KeyError, ZeroDivisionError, TypeError):
            return 1.5
    return 0.0


def stats() -> dict:
    sigs = _load_signals()["signals"]
    wins = [s for s in sigs if s.get("status") == "win"]
    losses = [s for s in sigs if s.get("status") == "loss"]
    expired = [s for s in sigs if s.get("status") == "expired"]
    closed = wins + losses + expired
    rs = [_r_of(s) for s in closed]
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    decided = len(wins) + len(losses)
    per_asset = {}
    for s in closed:
        a = per_asset.setdefault(s["asset"], {"win": 0, "loss": 0, "expired": 0, "r": 0.0})
        a[s["status"]] += 1
        a["r"] += _r_of(s)
    be = None
    rrs = [s["rr"] for s in closed if isinstance(s.get("rr"), (int, float)) and s["rr"] > 0]
    if rrs:
        be = 1 / (1 + sum(rrs) / len(rrs))
    return {
        "total": len(sigs),
        "open": len([s for s in sigs if s.get("status") == "open"]),
        "win": len(wins), "loss": len(losses), "expired": len(expired),
        "unknown": len([s for s in sigs if s.get("status") == "unknown"]),
        "closed": len(closed), "decided": decided,
        "win_rate": (len(wins) / decided) if decided else None,
        "win_rate_ci": wilson_interval(len(wins), decided) if decided else None,
        "total_r": sum(rs), "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "profit_factor": (gain / loss) if loss > 0 else (float("inf") if gain > 0 else None),
        "breakeven_win_rate": be,
        "per_asset": per_asset,
    }


# ------------------------------------------------------------------
# Watchlist persistante
# ------------------------------------------------------------------
def get_watchlist() -> list:
    with _lock:
        data = _load(WATCHLIST_FILE, None)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = [tuple(i) for i in data["items"]
                 if isinstance(i, (list, tuple)) and len(i) == 3]
        # une watchlist volontairement vidée (/watch clear) reste vide
        return items
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


# ------------------------------------------------------------------
# État persistant (le scan reste activé après un redémarrage du bot)
# ------------------------------------------------------------------
STATE_FILE = settings.DATA_DIR / "state.json"


def get_scan_enabled() -> bool:
    with _lock:
        data = _load(STATE_FILE, {})
    return bool(isinstance(data, dict) and data.get("scan_enabled"))


def set_scan_enabled(enabled: bool) -> None:
    with _lock:
        data = _load(STATE_FILE, {})
        if not isinstance(data, dict):
            data = {}
        data["scan_enabled"] = bool(enabled)
        _save(STATE_FILE, data)
