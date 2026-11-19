"""Flush fade backtest. Plan and judging: docs/flush-fade-test.md.
Run: .venv/bin/python backtest/flush_bt.py            (writes backtest/out/flush_report.txt)"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))
import flush  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
T0, TMID, T1 = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in ("2022-07-01", "2024-01-01", "2025-07-01"))
SEC = {"5m": 300, "15m": 900}
HOLD = {"5m": 1440, "15m": 3600}          # minutes
MINSTOP = {"5m": 0.002, "15m": 0.003}
TAKER, MAKER, STOPSLIP = 0.0005, 0.0002, 0.0002
DRAWS = 200


def load(coin):
    d = np.load(ROOT / f"user_data/data/flow/{coin}_1m.npz")
    m = {k: d[k] for k in d.files}
    f = pd.read_feather(ROOT / f"user_data/data/binance/futures/{coin}_USDT_USDT-1h-funding_rate.feather")
    m["ft"] = f.date.values.astype("datetime64[s]").astype(np.int64)
    m["fcum"] = np.r_[0.0, np.cumsum(f.funding_rate.values)]
    return m


def bars(m, sec):
    g = m["t"] // sec
    start = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    end = np.r_[start[1:], len(g)]
    full = (end - start) == sec // 60
    b = {"t": g[start] * sec, "o": m["o"][start], "h": np.maximum.reduceat(m["h"], start),
         "l": np.minimum.reduceat(m["l"], start), "c": m["c"][end - 1],
         "v": np.add.reduceat(m["v"], start), "tb": np.add.reduceat(m["tb"], start), "i1": end}
    return {k: x[full] for k, x in b.items()}


def first(mask):
    i = int(mask.argmax())
    return i if mask[i] else len(mask)


def simulate(m, i, sg, stop_ref, hold, cost=1.0, slip=0.0, minstop=0.002):
    """Enter at the open of 1m candle i. Works on prices multiplied by sg, so a short is a long on
    mirrored prices. Returns (R, exit index) or None."""
    n = len(m["t"])
    if i >= n - 2:
        return None
    e = m["o"][i] * (1 + sg * slip)
    es = sg * e
    s = min(sg * stop_ref, es - minstop * e)
    d = es - s
    if d <= 0:
        return None
    j = min(i + hold, n)
    hi, lo, op = (sg * m["h"][i:j], sg * m["l"][i:j], sg * m["o"][i:j]) if sg > 0 else \
                 (sg * m["l"][i:j], sg * m["h"][i:j], sg * m["o"][i:j])
    legs, pos, stop = [], 0, s           # legs: (exit price in mirrored units, fee rate)
    for tp in (es + d, es + 2 * d, es + 3 * d):
        a = pos + first(lo[pos:] <= stop)
        b = pos + first(hi[pos:] >= tp)
        if a <= b and a < len(lo):       # stopped (stop counted first)
            px = min(stop, op[a])
            legs += [(px, TAKER + STOPSLIP)] * (3 - len(legs))
            end = a
            break
        if b >= len(hi):                 # time ran out
            px = sg * m["c"][j - 1]
            legs += [(px, TAKER)] * (3 - len(legs))
            end = len(hi) - 1
            break
        legs.append((tp, MAKER))
        pos, stop, end = b, max(stop, es), b
        if pos + 1 < len(lo):
            pos += 1                     # the moved stop counts from the next candle
    gross = sum(px - es for px, _ in legs) / 3
    fees = cost * (TAKER * e + sum(abs(px) * f for px, f in legs) / 3)
    ta, tb_ = m["t"][i], m["t"][i + end] + 60
    fa, fb = np.searchsorted(m["ft"], ta, side="right"), np.searchsorted(m["ft"], tb_, side="right")
    fund = sg * (m["fcum"][fb] - m["fcum"][fa]) * e
    return (gross - fees - fund) / d, i + end


def trades(m, b, tf, k, confirm, flow=True, cost=1.0, slip=0.0):
    side, atr = flush.flush_side(b["o"], b["h"], b["l"], b["c"], b["v"], b["tb"], k, flow)
    out, busy = [], -1
    for x in np.flatnonzero(side):
        sg, y = int(side[x]), x
        if confirm:
            y = next((z for z in range(x + 1, min(x + 4, len(side)))
                      if sg * (b["c"][z] - b["o"][z]) > 0), None)
            if y is None:
                continue
        i = int(b["i1"][y])
        if i <= busy or i >= len(m["t"]):
            continue
        ext = b["l"][x:y + 1].min() if sg > 0 else b["h"][x:y + 1].max()
        r = simulate(m, i, sg, ext - sg * 0.25 * atr[x], HOLD[tf], cost, slip, MINSTOP[tf])
        if r is None:
            continue
        busy = r[1]
        out.append((int(b["t"][x]), sg, r[0]))
    return out


def control(m, b, tf, n_long, n_short, rng):
    """Random-time entries with the same stop rule, targets and time limit. Returns their R values."""
    _, atr = flush.flush_side(b["o"], b["h"], b["l"], b["c"], b["v"], b["tb"], 3)
    ok = np.flatnonzero((b["t"] >= T0) & (b["t"] < T1) & ~np.isnan(atr))
    out = []
    for sg, n in ((1, n_long), (-1, n_short)):
        for x in rng.choice(ok, n):
            ext = b["l"][x] if sg > 0 else b["h"][x]
            r = simulate(m, int(b["i1"][x]), sg, ext - sg * 0.25 * atr[x], HOLD[tf], minstop=MINSTOP[tf])
            if r:
                out.append(r[0])
    return out


def per_event(rows):
    """rows: (time, side, R, coin). Signals in the same hour are one event; returns one R per event."""
    if not rows:
        return np.array([])
    df = pd.DataFrame(rows, columns=["t", "sg", "r", "coin"])
    return df.groupby(df.t // 3600).r.mean().values


def main():
    data = {c: load(c) for c in COINS}
    B = {(c, tf): bars(data[c], SEC[tf]) for c in COINS for tf in SEC}
    lines, passed = [], []
    for nm, a, z in (("first half", T0, TMID), ("second half", TMID, T1)):
        t = data["BTC"]["t"]
        p0, p1 = data["BTC"]["c"][np.searchsorted(t, a)], data["BTC"]["c"][np.searchsorted(t, z) - 1]
        lines.append(f"BTC over the {nm}: {100 * (p1 / p0 - 1):+.0f}%")
    rng = np.random.default_rng(7)
    # Random-time control: a pool of 3,000 longs and 3,000 shorts per coin and timeframe, resampled to
    # the variant's number of events and its long/short mix.
    POOL = {}
    for tf in SEC:
        for sg in (1, -1):
            POOL[tf, sg] = np.array([r for c in COINS for r in
                                     control(data[c], B[c, tf], tf, 3000 if sg > 0 else 0, 3000 if sg < 0 else 0, rng)])
        print(tf, "control pool: long", round(POOL[tf, 1].mean(), 3), "short", round(POOL[tf, -1].mean(), 3), flush=True)
        lines.append(f"{tf} random-time entries: long {POOL[tf, 1].mean():+.3f}R, short {POOL[tf, -1].mean():+.3f}R per trade")
    for tf in SEC:
        for k in (3, 4):
            for confirm in (False, True):
                def run(flow=True, cost=1.0, slip=0.0, lo=T0, hi=T1):
                    return [(t, sg, r, c) for c in COINS
                            for t, sg, r in trades(data[c], B[c, tf], tf, k, confirm, flow, cost, slip) if lo <= t < hi]
                rows = run()
                ev = per_event(rows)
                h1, h2 = per_event([r for r in rows if r[0] < TMID]), per_event([r for r in rows if r[0] >= TMID])
                longs, shorts = [r[2] for r in rows if r[1] > 0], [r[2] for r in rows if r[1] < 0]
                coins_pos = sum(1 for c in COINS if np.mean([r[2] for r in rows if r[3] == c] or [-1]) > 0)
                nl, ns = len(longs), len(shorts)
                share = nl / max(1, nl + ns)
                ctl = [share * rng.choice(POOL[tf, 1], max(1, round(len(ev) * share))).mean()
                       + (1 - share) * rng.choice(POOL[tf, -1], max(1, round(len(ev) * (1 - share)))).mean()
                       for _ in range(DRAWS * 10)]
                p95 = float(np.percentile(ctl, 95))
                dbl = per_event(run(cost=2.0))
                ref = per_event(run(flow=False))
                m_ = lambda x: float(np.mean(x)) if len(x) else float("nan")
                ok = (len(ev) >= 100 and m_(h1) > 0 and m_(h2) > 0 and m_(ev) > p95 and m_(dbl) > 0 and coins_pos >= 4)
                name = f"{tf} K={k} {'confirm' if confirm else 'immediate'}"
                lines.append(
                    f"{name:22s} trades {len(rows):5d} events {len(ev):5d} | per event {m_(ev):+.3f}R "
                    f"(halves {m_(h1):+.3f} / {m_(h2):+.3f}) | long {m_(longs):+.3f} n={len(longs)} short {m_(shorts):+.3f} n={len(shorts)} "
                    f"| win {100 * np.mean([r[2] > 0 for r in rows]) if rows else 0:.0f}% | coins+ {coins_pos} "
                    f"| random times mean {np.mean(ctl):+.3f} p95 {p95:+.3f} | costs x2 {m_(dbl):+.3f} "
                    f"| price move only {m_(ref):+.3f} ({len(ref)} events) | {'PASS' if ok else 'fail'}")
                print(lines[-1], flush=True)
                if ok:
                    passed.append((m_(ev), name, tf, k, confirm, run))
    if passed:
        best = max(passed)
        _, name, tf, k, confirm, run = best
        held = [(t, sg, r, c) for c in COINS
                for t, sg, r in trades(data[c], B[c, tf], tf, k, confirm) if t >= T1]
        hv = per_event(held)
        lines.append(f"SELECTED {name}: held back from 2025-07-01: {len(held)} trades, {len(hv)} events, per event {np.mean(hv) if len(hv) else float('nan'):+.3f}R")
        for slip in (0.0002, 0.0005, 0.001, 0.002):
            lines.append(f"  extra entry slippage {100 * slip:.2f}%: per event {np.mean(per_event(run(slip=slip))):+.3f}R")
    else:
        lines.append("No variant passed. Nothing selected; the held-back period was not run.")
    (ROOT / "backtest/out/flush_report.txt").write_text("\n".join(lines) + "\n")
    print(lines[-1])


if __name__ == "__main__":
    main()
