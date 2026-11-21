"""Script backtest.py (ligne de commande), sans réseau : données CSV simulées."""
import pandas as pd
import pytest

import backtest as cli
from synthetic import make_ohlc


@pytest.fixture
def csv_path(tmp_path):
    df = make_ohlc(1500, seed=5, drift=0.0002, vol=0.003)
    df["time"] = df["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    p = tmp_path / "bougies.csv"
    df.to_csv(p, index=False)
    return p


def test_load_csv_iso_et_timestamps(tmp_path, csv_path):
    df = cli._load_csv(str(csv_path))
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"] and len(df) == 1500
    ts = df.copy()
    secondes = int(df["time"].iloc[0].timestamp())
    for mult in (1, 1000):                                      # secondes, millisecondes
        ts["time"] = [secondes * mult + i * 900 * mult for i in range(len(ts))]
        p = tmp_path / f"ts{mult}.csv"
        ts.to_csv(p, index=False)
        assert cli._load_csv(str(p))["time"].iloc[0] == df["time"].iloc[0]


def test_load_csv_colonnes_manquantes(tmp_path):
    p = tmp_path / "x.csv"
    pd.DataFrame({"time": [1], "close": [1.0]}).to_csv(p, index=False)
    with pytest.raises(SystemExit):
        cli._load_csv(str(p))


def test_cli_fichier_export_et_courbe(csv_path, tmp_path, capsys):
    out_csv, out_png = tmp_path / "t.csv", tmp_path / "c.png"
    rc = cli.main(["--file", str(csv_path), "--tf", "15m", "--cost", "0.05", "--mtf",
                   "--export", str(out_csv), "--plot", str(out_png)])
    assert rc == 0
    txt = capsys.readouterr().out
    assert "BACKTEST" in txt and "jamais une promesse" in txt
    assert out_csv.exists() and out_png.read_bytes()[:4] == b"\x89PNG"
    assert {"r", "status", "direction"} <= set(pd.read_csv(out_csv).columns)


def test_cli_sans_argument_affiche_l_aide(capsys):
    assert cli.main([]) == 2


def test_cli_actif_inconnu():
    with pytest.raises(SystemExit):
        cli.main(["zzzzzz", "1h"])
