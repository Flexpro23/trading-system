"""Open interest event study (plan: docs/open-interest-test.md).

Run:  .venv/bin/python -I backtest/oi_bt.py            (training period only)
      .venv/bin/python -I backtest/oi_bt.py --coverage (data coverage only, no results)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PX = ROOT / "user_data" / "data" / "binance" / "futures"
OI = ROOT / "user_data" / "data" / "oi"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
TRAIN_END = pd.Timestamp("2025-07-01", tz="UTC")       # nothing from here on is read
Z = 2.0
SPAN = 4                 # hours of the price move and of the open-interest change
SD_WIN = 720             # 30 days of 1h candles
RANK_WIN = 2160          # 90 days
HOLDS = (24, 72)


def load(coin):
    p = pd.read_feather(PX / f"{coin}_USDT_USDT-1h-futures.feather")
    p = p[p["date"] < TRAIN_END].reset_index(drop=True)
    z = np.load(OI / f"{coin}_5m.npz")
    oi = pd.Series(z["oi"], index=z["t"].astype(np.int64))
    oi = oi[oi > 0]
    t = p["date"].dt.as_unit("s").astype("int64").to_numpy()
    now = t + 3600 - 300                       # last row at least 5 minutes before the close
    grid = np.arange(oi.index.min(), oi.index.max() + 300, 300)
    s = oi.reindex(grid).ffill(limit=2)        # bridge at most 10 minutes; longer gaps stay empty
    a = s.reindex(now).to_numpy()
    b = s.reindex(now - SPAN * 3600).to_numpy()
    p["chg"] = a / b - 1
    past = p["chg"].shift(1).rolling(RANK_WIN, min_periods=1500)
    for q in (5, 20, 80, 95):
        p[f"q{q}"] = past.quantile(q / 100)
    r = p["close"] / p["close"].shift(SPAN) - 1
    p["z"] = r / r.shift(1).rolling(SD_WIN).std()
    f = pd.read_feather(PX / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    f = f[f["funding_rate"] != 0]
    fund = pd.Series(f["funding_rate"].to_numpy(), index=f["date"].dt.as_unit("s").astype("int64").to_numpy())
    p["cumfund"] = fund.reindex(t).fillna(0).cumsum().to_numpy()   # funding paid at the open of that hour
    p["t"] = t
    return p


def trades(p, long_sig, short_sig, hold, coin):
    """Enter at the next 1h open, leave `hold` hours later. One position at a time."""
    o, cf, t = p["open"].to_numpy(), p["cumfund"].to_numpy(), p["t"].to_numpy()
    side = np.where(long_sig, 1, np.where(short_sig, -1, 0))
    out, free, n = [], 0, len(p)
    for i in np.nonzero(side)[0]:
        e, x = i + 1, i + 1 + hold
        if e < free or x >= n:
            continue
        sg = side[i]
        gross = sg * (o[x] / o[e] - 1)
        funding = -sg * (cf[x] - cf[e])        # longs pay positive funding; payments in [entry, exit)
        out.append((t[e], coin, sg, gross, funding))
        free = x
    return out


def signals(p, kind):
    ok = p["chg"].notna() & p["q5"].notna() & p["z"].notna()
    dn, up = ok & (p["z"] <= -Z), ok & (p["z"] >= Z)
    low, high = p["chg"] <= p["q5"], p["chg"] >= p["q95"]
    mid = (p["chg"] > p["q20"]) & (p["chg"] < p["q80"])
    return {"H1": (dn & low, up & low), "H1 price alone": (dn, up), "H1 middle OI": (dn & mid, up & mid),
            "H2": (up & high, dn & high), "H2 price alone": (up, dn), "H2 middle OI": (up & mid, dn & mid)}[kind]


def per_event(d, cost):
    net = d["gross"] + d["funding"] - cost
    return net.groupby(d["t"]).mean()


def main():
    data = {c: load(c) for c in COINS}
    start = max(pd.to_datetime(p.loc[p["chg"].notna(), "t"].iloc[0], unit="s", utc=True) for p in data.values())
    print("Coverage (first hour with a 4h open-interest change, share of hours with one after that):")
    for c, p in data.items():
        has = p["chg"].notna()
        first = has.idxmax()
        print(f"  {c}: {p['date'][first]:%Y-%m-%d}  {100 * has[first:].mean():.1f}%")
    half = start + (TRAIN_END - start) / 2
    print(f"Training {start:%Y-%m-%d} to 2025-06-30, halves split at {half:%Y-%m-%d}")
    if "--coverage" in sys.argv:
        return
    hs = half.timestamp()
    for hold in HOLDS:
        print(f"\n=== hold {hold}h ===   net of 0.10% and funding, % per event")
        res = {}
        for kind in ("H1", "H1 price alone", "H1 middle OI", "H2", "H2 price alone", "H2 middle OI"):
            rows = []
            for c, p in data.items():
                rows += trades(p, *[s.to_numpy() for s in signals(p, kind)], hold, c)
            d = pd.DataFrame(rows, columns=["t", "coin", "side", "gross", "funding"])
            d = d[d["t"] >= start.timestamp()]
            res[kind] = d
            ev = per_event(d, 0.001)
            a, b = ev[ev.index < hs], ev[ev.index >= hs]
            ls = [100 * (d.loc[d["side"] == s, "gross"] + d.loc[d["side"] == s, "funding"] - 0.001).mean() for s in (1, -1)]
            coins = d.assign(net=d["gross"] + d["funding"] - 0.001).groupby("coin")["net"].mean()
            print(f"{kind:16s} trades {len(d):5d} events {len(a):4d}/{len(b):4d}  all {100 * ev.mean():+.3f}  "
                  f"halves {100 * a.mean():+.3f} / {100 * b.mean():+.3f}  long {ls[0]:+.3f} short {ls[1]:+.3f}  "
                  f"gross {100 * d['gross'].mean():+.3f}  at 0.20% {100 * per_event(d, 0.002).mean():+.3f}  "
                  f"coins+ {(coins > 0).sum()}/6")
        for h in ("H1", "H2"):
            s, k = per_event(res[h], 0.001), per_event(res[h + " price alone"], 0.001)
            diff = s.mean() - k.mean()
            se = np.sqrt(s.var() / len(s) + k.var() / len(k))
            for nm, lo, hi in (("first half", -np.inf, hs), ("second half", hs, np.inf)):
                s2, k2 = s[(s.index >= lo) & (s.index < hi)], k[(k.index >= lo) & (k.index < hi)]
                print(f"  {h} minus price alone, {nm}: {100 * (s2.mean() - k2.mean()):+.3f}")
            print(f"  {h} minus price alone, whole period: {100 * diff:+.3f}  ({diff / se:+.2f} standard errors)")


if __name__ == "__main__":
    main()
