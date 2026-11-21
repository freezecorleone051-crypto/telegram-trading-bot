"""Historique des signaux, suivi TP/SL, statistiques, watchlist."""
import json

import pandas as pd
import pytest

from storage import history


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "SIGNALS_FILE", tmp_path / "signals.json")
    monkeypatch.setattr(history, "WATCHLIST_FILE", tmp_path / "watchlist.json")
    monkeypatch.setattr(history, "STATE_FILE", tmp_path / "state.json")
    return tmp_path


def _sig(direction="ACHAT", bar_time="2025-01-01T10:00:00+00:00", entry=100.0, tp=103.0, sl=98.0):
    return {"direction": direction, "confidence": 80, "score": 9, "regime": "tendance",
            "entry": entry, "tp": tp, "sl": sl, "rr": 1.5, "bar_time": bar_time}


def _bars(rows, start="2025-01-01 10:05", freq="5min"):
    """rows = [(open, high, low, close), ...] à partir de `start`."""
    t = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame({"time": t, "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows], "volume": 1.0})


# ---------------------------------------------------------------- évaluation
def test_win():
    df = _bars([(100, 101, 99.5, 100.5), (100.5, 103.5, 100, 103)])
    r = history.evaluate_signal({**_sig(), "bar_time": "2025-01-01T10:00:00+00:00"}, df, "5m")
    assert r["status"] == "win" and r["r"] == pytest.approx(1.5)


def test_loss():
    df = _bars([(100, 100.5, 97.5, 98)])
    assert history.evaluate_signal(_sig(), df, "5m")["status"] == "loss"


def test_tp_et_sl_dans_la_meme_bougie_compte_comme_perte():
    df = _bars([(100, 104, 97, 100)])
    r = history.evaluate_signal(_sig(), df, "5m")
    assert r["status"] == "loss" and r["r"] == -1.0


def test_vente_win_et_loss():
    s = _sig("VENTE", entry=100, tp=97, sl=102)
    assert history.evaluate_signal(s, _bars([(100, 100.5, 96.5, 97)]), "5m")["status"] == "win"
    assert history.evaluate_signal(s, _bars([(100, 102.5, 99.5, 102)]), "5m")["status"] == "loss"


def test_signal_encore_ouvert():
    df = _bars([(100, 101, 99.5, 100.5)] * 5)
    assert history.evaluate_signal(_sig(), df, "5m") is None


def test_signal_expire_avec_resultat_reel():
    n = history.EXPIRY_BARS["5m"]
    df = _bars([(100, 101, 99.5, 100.9)] * n)
    r = history.evaluate_signal(_sig(), df, "5m")
    assert r["status"] == "expired" and r["r"] == pytest.approx(0.9 / 2.0)


def test_historique_trop_court_donne_unknown():
    df = _bars([(100, 101, 99.5, 100.5)] * 5, start="2025-01-01 14:00")   # commence 4 h après le signal
    assert history.evaluate_signal(_sig(), df, "5m")["status"] == "unknown"


def test_bougie_du_signal_ignoree():
    # la bougie dont l'heure == bar_time est AVANT l'entrée : un TP dedans ne compte pas
    df = _bars([(100, 105, 99.5, 100), (100, 100.5, 99.5, 100)], start="2025-01-01 10:00")
    assert history.evaluate_signal(_sig(), df, "5m") is None


# ---------------------------------------------------------------- stockage
def test_add_signal_et_anti_doublon(tmp_path):
    assert history.add_signal(_sig(), "crypto", "BTC/USDT", "5m", "src")
    assert not history.add_signal(_sig(bar_time="2025-01-01T10:05:00+00:00"), "crypto", "BTC/USDT", "5m", "src")  # déjà ouvert
    assert history.add_signal(_sig("VENTE", entry=100, tp=97, sl=102), "crypto", "BTC/USDT", "5m", "src")


def test_meme_bougie_pas_renvoyee_apres_cloture():
    history.add_signal(_sig(), "crypto", "BTC/USDT", "5m", "src")
    df = _bars([(100, 103.5, 99.5, 103)])
    closed = history.update_open_signals(lambda *a, **k: (df, "x"))
    assert len(closed) == 1 and closed[0]["status"] == "win"
    # le même signal sur la même bougie ne revient pas
    assert not history.add_signal(_sig(), "crypto", "BTC/USDT", "5m", "src")
    # une nouvelle bougie, oui
    assert history.add_signal(_sig(bar_time="2025-01-01T11:00:00+00:00"), "crypto", "BTC/USDT", "5m", "src")


def test_ids_jamais_reutilises():
    for i in range(5):
        history.add_signal(_sig(bar_time=f"2025-01-01T1{i}:00:00+00:00", entry=100 + i, tp=103 + i, sl=98 + i),
                           "crypto", f"A{i}", "5m", "src")
    data = json.loads(history.SIGNALS_FILE.read_text())
    data["signals"] = data["signals"][-2:]                    # purge des anciens
    history.SIGNALS_FILE.write_text(json.dumps(data))
    history.add_signal(_sig(), "crypto", "NEW", "5m", "src")
    ids = [s["id"] for s in json.loads(history.SIGNALS_FILE.read_text())["signals"]]
    assert len(ids) == len(set(ids))


def test_fichier_corrompu_est_conserve_pas_ecrase(tmp_path):
    history.add_signal(_sig(), "crypto", "BTC/USDT", "5m", "src")
    raw = history.SIGNALS_FILE.read_text()
    history.SIGNALS_FILE.write_text(raw[:-7])                  # fichier tronqué
    history.add_signal(_sig(), "crypto", "ETH/USDT", "5m", "src")
    saved = list(tmp_path.glob("signals.json.corrupt-*"))
    assert saved, "le fichier illisible doit être mis de côté, pas détruit"
    assert "BTC/USDT" in saved[0].read_text()


def test_ecriture_atomique_sans_fichier_temporaire_restant(tmp_path):
    history.add_signal(_sig(), "crypto", "BTC/USDT", "5m", "src")
    assert not list(tmp_path.glob("*.tmp"))


def test_stats_en_R():
    for k, st in enumerate(["win", "win", "loss", "loss", "loss"]):
        history.add_signal(_sig(bar_time=f"2025-01-02T0{k}:00:00+00:00"), "crypto", f"A{k}", "5m", "src")
    data = json.loads(history.SIGNALS_FILE.read_text())
    for s, st in zip(data["signals"], ["win", "win", "loss", "loss", "loss"]):
        s["status"] = st
        s["r"] = 1.5 if st == "win" else -1.0
    history.SIGNALS_FILE.write_text(json.dumps(data))
    st = history.stats()
    assert st["win"] == 2 and st["loss"] == 3 and st["closed"] == 5
    assert st["total_r"] == pytest.approx(0.0)       # 2 x 1.5 - 3 x 1.0
    assert st["avg_r"] == pytest.approx(0.0)
    assert st["profit_factor"] == pytest.approx(1.0)
    assert st["win_rate"] == pytest.approx(0.4)
    assert st["breakeven_win_rate"] == pytest.approx(0.4)   # 1 / (1 + 1.5)
    lo, hi = st["win_rate_ci"]
    assert lo < 0.4 < hi


def test_stats_anciens_fichiers_v1_sans_champ_r():
    history.SIGNALS_FILE.write_text(json.dumps({"signals": [
        {"id": 1, "asset": "A", "tf": "5m", "status": "win", "entry": 100, "tp": 103, "sl": 98},
        {"id": 2, "asset": "A", "tf": "5m", "status": "loss", "entry": 100, "tp": 103, "sl": 98}]}))
    st = history.stats()
    assert st["total_r"] == pytest.approx(0.5)


# ---------------------------------------------------------------- watchlist
def test_watchlist_clear_reste_vide():
    """v1 : après /watch clear, la watchlist par défaut réapparaissait."""
    assert history.get_watchlist()                        # défauts
    history.watchlist_clear()
    assert history.get_watchlist() == []
    assert history.watchlist_add("stocks", "NVDA", "1h")
    assert not history.watchlist_add("stocks", "NVDA", "1h")
    assert history.get_watchlist() == [("stocks", "NVDA", "1h")]
    assert history.watchlist_remove("stocks", "NVDA", "1h")
    assert not history.watchlist_remove("stocks", "NVDA", "1h")


def test_etat_du_scan_persistant():
    assert history.get_scan_enabled() is False
    history.set_scan_enabled(True)
    assert history.get_scan_enabled() is True
