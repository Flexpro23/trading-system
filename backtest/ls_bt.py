"""Long/short account ratio event study (plan: docs/long-short-ratio-test.md).

Run:  .venv/bin/python -I backtest/ls_bt.py [--coverage]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oi_bt import COINS, OI, PX, TRAIN_END, per_event, trades  # noqa: E402

START = pd.Timestamp("2021-12-01", tz="UTC").timestamp()
HALF = pd.Timestamp("2023-09-16", tz="UTC").timestamp()
RANK_WIN = 2160
DRAWS = 200


def load(coin):
    p = pd.read_feather(PX / f"{coin}_USDT_USDT-1h-futures.feather")
    p = p[p["date"] < TRAIN_END].reset_index(drop=True)
    t = p["date"].dt.as_unit("s").astype("int64").to_numpy()
    z = np.load(OI / f"{coin}_5m.npz")
    for col in ("ls", "top_ls"):
        s = pd.Series(z[col], index=z["t"].astype(np.int64))
        s = s[s > 0]
        s = s.reindex(np.arange(s.index.min(), s.index.max() + 300, 300)).ffill(limit=2)
        p[col] = s.reindex(t + 3600 - 300).to_numpy()
        past = p[col].shift(1).rolling(RANK_WIN, min_periods=1500)
        for q in (5, 20, 80, 95):
            p[f"{col}_q{q}"] = past.quantile(q / 100)
    r3 = p["close"] / p["close"].shift(72) - 1
    past = r3.shift(1).rolling(RANK_WIN, min_periods=1500)
    p["r3"], p["r3_q5"], p["r3_q95"] = r3, past.quantile(0.05), past.quantile(0.95)
    f = pd.read_feather(PX / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    f = f[f["funding_rate"] != 0]
    fund = pd.Series(f["funding_rate"].to_numpy(), index=f["date"].dt.as_unit("s").astype("int64").to_numpy())
    p["cumfund"] = fund.reindex(t).fillna(0).cumsum().to_numpy()
    p["t"] = t
    return p


def signals(p, kind):
    """(long, short)"""
    if kind == "L1 fade crowd":
        return p["ls"] <= p["ls_q5"], p["ls"] >= p["ls_q95"]
    if kind == "L1 price version":
        return p["r3"] >= p["r3_q95"], p["r3"] <= p["r3_q5"]
    if kind == "L2 follow top":
        return p["top_ls"] >= p["top_ls_q95"], p["top_ls"] <= p["top_ls_q5"]
    if kind == "L3 top vs crowd":
        return ((p["top_ls"] >= p["top_ls_q80"]) & (p["ls"] <= p["ls_q20"]),
                (p["top_ls"] <= p["top_ls_q20"]) & (p["ls"] >= p["ls_q80"]))
    raise ValueError(kind)


def random_draws(data, d, hold, rng):
    """Mean net result per trade of the same number of longs and shorts per coin and half at random hours."""
    pools = {}
    for c, p in data.items():
        o, cf, t = p["open"].to_numpy(), p["cumfund"].to_numpy(), p["t"].to_numpy()
        e = np.arange(1, len(p) - hold - 1)
        e = e[t[e] >= START]
        gross = o[e + hold] / o[e] - 1
        fund = cf[e + hold] - cf[e]
        first = t[e] < HALF
        pools[c] = {(sg, h): (sg * gross - sg * fund - 0.001)[first if h else ~first]
                    for sg in (1, -1) for h in (True, False)}
    counts = d.assign(h=d["t"] < HALF).groupby(["coin", "side", "h"]).size()
    out = np.zeros(DRAWS)
    for (c, sg, h), n in counts.items():
        out += rng.choice(pools[c][(sg, h)], size=(DRAWS, n)).sum(axis=1)
    return out / len(d)


def main():
    data = {c: load(c) for c in COINS}
    print("Coverage (first hour with a reading, share of hours with one after that):")
    for c, p in data.items():
        for col in ("ls", "top_ls"):
            has = p[col].notna()
            print(f"  {c} {col}: {p['date'][has.idxmax()]:%Y-%m-%d}  {100 * has[has.idxmax():].mean():.1f}%")
    if "--coverage" in sys.argv:
        return
    rng = np.random.default_rng(7)
    for kind, holds in (("L1 fade crowd", (24, 72)), ("L1 price version", (24, 72)),
                        ("L2 follow top", (24, 72)), ("L3 top vs crowd", (72,))):
        for hold in holds:
            rows = []
            for c, p in data.items():
                lg, sh = signals(p, kind)
                rows += trades(p, lg.to_numpy(), sh.to_numpy() & ~lg.to_numpy(), hold, c)
            d = pd.DataFrame(rows, columns=["t", "coin", "side", "gross", "funding"])
            d = d[d["t"] >= START]
            net = d["gross"] + d["funding"] - 0.001
            ev = per_event(d, 0.001)
            a, b = ev[ev.index < HALF], ev[ev.index >= HALF]
            coins = net.groupby(d["coin"]).mean()
            rnd = random_draws(data, d, hold, rng)
            print(f"{kind:17s} {hold:2d}h trades {len(d):5d} events {len(a):4d}/{len(b):4d}  "
                  f"halves {100 * a.mean():+.3f} / {100 * b.mean():+.3f}  per trade {100 * net.mean():+.3f}  "
                  f"random median {100 * np.median(rnd):+.3f} p95 {100 * np.quantile(rnd, 0.95):+.3f}  "
                  f"long {100 * net[d['side'] == 1].mean():+.3f} (n {(d['side'] == 1).sum()}) "
                  f"short {100 * net[d['side'] == -1].mean():+.3f} (n {(d['side'] == -1).sum()})  "
                  f"at 0.20% {100 * per_event(d, 0.002).mean():+.3f}  coins+ {(coins > 0).sum()}/6")


if __name__ == "__main__":
    main()
