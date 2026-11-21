"""Backtest en ligne de commande : teste le moteur de signaux sur l'historique.

Exemples :
  python backtest.py NVDA 1h                       # 1 actif, 2000 bougies, coûts par défaut
  python backtest.py "BTC/USDT" 15m --bars 4000 --cost 0.12
  python backtest.py EURUSD 15m --mtf              # avec filtre du timeframe supérieur
  python backtest.py --family actions --tf 1h      # compare TOUTES les actions d'une famille
  python backtest.py NVDA 1h --plot nvda.png --export trades.csv
  python backtest.py --file mes_bougies.csv --tf 15m --cost 0.05   # tes propres données

Le fichier CSV (--file) doit avoir les colonnes : time, open, high, low, close
(et volume si disponible). `time` en ISO (2025-01-31 14:00:00) ou timestamp.

RAPPEL : un backtest décrit le PASSÉ. Un bon résultat n'est pas une promesse ;
un mauvais résultat, lui, est une vraie information. Moins de ~30 trades =
résultat peu fiable (l'intervalle de confiance le montre).
"""
import argparse
import sys

import pandas as pd

from data import feed, markets
from services import backtest as bt
from core.signal_engine import InsufficientData


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise SystemExit(f"Colonnes manquantes dans {path} : {', '.join(sorted(missing))}")
    t = df["time"]
    if pd.api.types.is_numeric_dtype(t):
        v = abs(float(t.iloc[0]))
        unit = "ns" if v > 1e17 else "us" if v > 1e14 else "ms" if v > 1e11 else "s"
        df["time"] = pd.to_datetime(t, unit=unit, utc=True)
    else:
        df["time"] = pd.to_datetime(t, utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    return df[["time", "open", "high", "low", "close", "volume"]]


def _run_one(market, asset, tf, args, df=None, source="fichier"):
    if df is None:
        df, source = feed.fetch_history(market, asset, tf, args.bars)
    cost = args.cost if args.cost is not None else bt.DEFAULT_COST_PCT.get(market, 0.05)
    use_mtf = args.mtf and market in markets.MARKET_ORDER
    out = bt.run_backtest(df, tf, market, cost, None, 80, use_mtf,
                          markets.decimals_for(market, asset) if asset else None)
    return df, source, out


def _plot(trades, title, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    eq = [0.0]
    for t in trades:
        eq.append(eq[-1] + t["r"])
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(range(len(eq)), eq, lw=1.6)
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_title(f"{title} — courbe de résultat cumulé (en R, coûts inclus)")
    ax.set_xlabel("numéro du trade")
    ax.set_ylabel("R cumulés")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(f"Courbe enregistrée : {path}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Backtest du moteur de signaux (coûts inclus).")
    p.add_argument("asset", nargs="*", help="actif (ex : NVDA, 'BTC/USDT', eurusd, or, dax)")
    p.add_argument("--tf", help="timeframe (ex : 15m, 1h, 1d)")
    p.add_argument("--bars", type=int, default=2000, help="nombre de bougies (défaut 2000)")
    p.add_argument("--cost", type=float, default=None,
                   help="coût aller-retour en %% du prix (défaut : valeur prudente par famille)")
    p.add_argument("--mtf", action="store_true", help="filtre du timeframe supérieur (comme en direct)")
    p.add_argument("--family", help="teste toute une famille : crypto, actions, etf, indices, matieres, forex")
    p.add_argument("--file", help="CSV de bougies à utiliser au lieu du téléchargement")
    p.add_argument("--export", help="enregistre la liste des trades en CSV")
    p.add_argument("--plot", help="enregistre la courbe de résultat cumulé (PNG)")
    args = p.parse_args(argv)

    tokens = list(args.asset)
    tf = markets.parse_timeframe(args.tf) if args.tf else None
    if tokens and not tf and markets.parse_timeframe(tokens[-1]):
        tf = markets.parse_timeframe(tokens.pop())
    query = " ".join(tokens)

    # --- mode famille -------------------------------------------------
    if args.family:
        market = markets.parse_market(args.family)
        if not market:
            raise SystemExit("Famille inconnue : crypto, actions, etf, indices, matieres, forex, otc")
        tf = tf or markets.default_timeframe(market)
        rows = []
        for a in markets.assets_for(market):
            try:
                _, _, out = _run_one(market, a, tf, args)
            except Exception as e:      # un actif indisponible ne bloque pas les autres
                print(f"  {a:<12} indisponible ({e})")
                continue
            s = out["summary"]
            rows.append((a, s["trades"], s["avg_r"], s["total_r"], s["max_drawdown_r"]))
            print(f"  {a:<12} {s['trades']:>4} trades   {s['avg_r']:+.2f} R/trade   "
                  f"total {s['total_r']:+7.1f} R   drawdown {s['max_drawdown_r']:.1f} R")
        if rows:
            ok = [r for r in rows if r[1] >= 30 and r[2] > 0]
            print(f"\n{len(ok)} actif(s) sur {len(rows)} avec ≥ 30 trades et espérance > 0 "
                  "(après coûts). Attention : tester beaucoup d'actifs et garder les meilleurs "
                  "revient à choisir le hasard favorable — confirme sur une AUTRE période.")
        return 0

    # --- mode fichier -------------------------------------------------
    if args.file:
        if not tf:
            raise SystemExit("Avec --file, indique le timeframe : --tf 15m")
        market = "crypto"
        if query:
            m = markets.find_asset(query)
            market = m[0] if m else "crypto"
        df, source, out = _run_one(market, None, tf, args, _load_csv(args.file),
                                   "fichier " + args.file)
        asset = query or "fichier"
    else:
        if not query:
            p.print_help()
            return 2
        match = markets.find_asset(query)
        if not match:
            raise SystemExit(f"Actif non reconnu : {query!r} (liste : voir /marches dans le bot)")
        market, asset = match
        tf = tf or markets.default_timeframe(market)
        if tf not in markets.fetchable_timeframes(market):
            raise SystemExit(f"{asset} n'existe pas en {tf}. Disponibles : "
                             + ", ".join(markets.timeframes_for(market)))
        try:
            df, source, out = _run_one(market, asset, tf, args)
        except (feed.FeedError, InsufficientData) as e:
            raise SystemExit(f"Impossible : {e}")

    print(bt.format_report(asset, tf, out["summary"], source))
    print("\nSimulation sur le passé : jamais une promesse pour le futur.")
    if args.export and out["trades"]:
        pd.DataFrame(out["trades"]).to_csv(args.export, index=False)
        print(f"Trades exportés : {args.export}")
    if args.plot and out["trades"]:
        _plot(out["trades"], f"{asset} {tf}", args.plot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
