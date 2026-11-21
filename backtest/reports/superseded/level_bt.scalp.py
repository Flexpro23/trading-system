"""Backtest of your level method on 5m entries (docs/level-method.md, frozen plan).

Run:  .venv/bin/python backtest/level_bt.py run [--pilot]      (writes backtest/out/<coin>.pkl)
      .venv/bin/python backtest/level_bt.py report               (selection on training data, then held-back)
Shorts are simulated as longs on mirrored prices (-price), so every rule is written once.
"""
import itertools
import multiprocessing as mp
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import snapshots as S

OUT = Path(__file__).resolve().parent / "out"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
day = lambda s: int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
START, SPLIT, END = day("2022-07-01"), day("2025-07-01"), day("2026-11-15")

TRIGGERS = ("first", "close", "div", "pattern")
STOPS = ("atr", "next", "pct")
TARGETS = ("levels", "fib", "r")
TRENDS = ("none", "ema200")
WIDTHS = (0.003, 0.005, "atr")
STRENGTHS = (1, 2, 3)
TOUCHES = (2, 3)

import os
MAKER, TAKER, SLIP = (0.0, 0.0, 0.0) if os.environ.get("NOFEES") else (0.0002, 0.0005, 0.0002)
THROUGH = 0.0002          # a first-touch limit fills only if price trades this far through it
WAIT = 36                 # bars a waiting trigger may take after the touch (3 hours)
HOLD = 288                # bars before a trade is closed (24 hours)
MIN_STOP = 0.002
PCT_STOP = 0.005
FIB_TP = (0.382, 0.5, 0.618)


def wilder_rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = S.rma(np.maximum(d, 0), n), S.rma(np.maximum(-d, 0), n)
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


def pivot_lows(lw, w=2):
    n = len(lw)
    out = np.zeros(n, bool)
    for k in range(w, n - w):
        if lw[k] == lw[k - w:k + w + 1].min():
            out[k] = True
    return out


class Side:
    """Price arrays seen from one side: longs as is, shorts mirrored."""

    def __init__(self, a, sign, atr5, rsi):
        self.sign = sign
        if sign > 0:
            self.O, self.H, self.L, self.C, self.R = a["o"], a["h"], a["l"], a["c"], rsi
        else:
            self.O, self.H, self.L, self.C, self.R = -a["o"], -a["l"], -a["h"], -a["c"], 100 - rsi
        self.atr = atr5
        self.PL = pivot_lows(self.L)
        self.n = len(self.C)


# ------------------------------------------------------------------ triggers (long view)

def trigger(sd: Side, kind, i, zl, zh):
    """Entry after a touch of zone [zl, zh] at bar i. Returns (bar, price, fee, intrabar, low_since_touch) or None."""
    O, H, L, C, n = sd.O, sd.H, sd.L, sd.C, sd.n
    ref = abs(zh)
    if kind == "first":
        if L[i] > zh - THROUGH * ref:
            return None
        return (i, min(O[i], zh), MAKER, True, L[i])
    cancel = zl - sd.atr[i - 1]
    tol = max(0.0025 * ref, zh - zl)
    neck = None
    for j in range(i, min(i + WAIT, n - 1)):
        if C[j] < cancel:
            return None
        if kind == "close":
            if C[j] > zh:
                return (j, C[j], TAKER, False, L[i:j + 1].min())
            continue
        if kind == "pattern" and neck is not None and C[j] > neck:
            return (j, C[j], TAKER, False, L[i:j + 1].min())
        k = j - 2
        if k < i or not sd.PL[k] or L[k] > zh + tol:
            continue
        prev = [p for p in range(max(0, k - 60), k - 3) if sd.PL[p]]
        if kind == "div":
            p = next((p for p in reversed(prev) if k - p <= 40 and L[p] > L[k]), None)
            if p is not None and sd.R[p] < sd.R[k] and sd.R[p] < 40:
                return (j, C[j], TAKER, False, L[i:j + 1].min())
            continue
        # patterns: double / triple bottom, inverse head and shoulders (tops and H&S on the mirrored side)
        ps = [p for p in prev if k - p >= 6][-2:] + [k]
        if len(ps) >= 2:
            a, b = ps[-2], ps[-1]
            if abs(L[a] - L[b]) <= tol:
                nk = H[a:b + 1].max()
                if len(ps) == 3 and abs(L[ps[0]] - L[b]) <= tol:
                    nk = H[ps[0]:b + 1].max()          # triple bottom
                if nk - max(L[a], L[b]) >= 0.003 * ref:
                    neck = nk
        if len(ps) == 3:
            p1, p2, p3 = ps
            if L[p2] < min(L[p1], L[p3]) - 0.001 * ref and abs(L[p1] - L[p3]) <= tol:
                neck = max(H[p1:p2 + 1].max(), H[p2:p3 + 1].max())
    return None


# ------------------------------------------------------------------ stops, targets, exits (long view)

def stop_price(sd, kind, j, e, zl, levels_below):
    a = sd.atr[j - 1]
    ref = abs(e)
    if kind == "atr":
        s = zl - a
    elif kind == "next":
        below = [p for p in levels_below if p < zl]
        if not below:
            return None
        s = max(below) - 0.25 * a
        if e - s > 0.03 * ref:
            return None
    else:
        s = e - PCT_STOP * ref
    if s >= e:
        return None
    return min(s, e - MIN_STOP * ref)


def targets(sd, kind, i, e, risk, low, zones_above):
    ref = abs(e)
    tp = []
    if kind == "levels":
        tp = sorted({zl for zl in zones_above if zl > e + 0.001 * ref})[:3]
    elif kind == "fib":
        hi = sd.H[max(0, i - 288):i].max() if i > 0 else e
        tp = [low + r * (hi - low) for r in FIB_TP]
        tp = [t for t in tp if t > e + 0.001 * ref]
    return tp or [e + risk, e + 2 * risk, e + 3 * risk]


def exit_trade(sd, j, e, s, tps, fee_in, intrabar):
    """Walk the trade forward. Stop first when a candle reaches both. Equal split over targets,
    stop to entry after the first target, closed after HOLD bars. Returns (exit_bar, R)."""
    start, end = (j if intrabar else j + 1), min(j + HOLD, sd.n - 1)
    if start > end:
        return None
    Lw, Hs = sd.L[start:end + 1], sd.H[start:end + 1]
    risk = e - s
    ref = abs(e)
    stop, rem, pnl, fees, pos, left = s, 1.0, 0.0, fee_in * ref, 0, list(tps)
    first = True
    while True:
        sh = np.flatnonzero(Lw[pos:] <= stop)
        tp_from = pos + 1 if (intrabar and pos == 0) else pos
        th = np.flatnonzero(Hs[tp_from:] >= left[0])
        si = pos + sh[0] if len(sh) else None
        ti = tp_from + th[0] if len(th) else None
        if si is not None and (ti is None or si <= ti):
            pnl += rem * (stop - e)
            fees += rem * (TAKER + SLIP) * abs(stop)
            return start + si, (pnl - fees) / risk
        if ti is None:
            px = sd.C[end]
            pnl += rem * (px - e)
            fees += rem * TAKER * abs(px)
            return end, (pnl - fees) / risk
        while left and Hs[ti] >= left[0]:
            frac = rem / len(left)
            pnl += frac * (left[0] - e)
            fees += frac * MAKER * abs(left[0])
            rem -= frac
            left.pop(0)
        if not left:
            return start + ti, (pnl - fees) / risk
        if first:
            stop, first = max(stop, e), False
        pos = ti + 1
        if pos > end - start:
            px = sd.C[end]
            pnl += rem * (px - e)
            fees += rem * TAKER * abs(px)
            return end, (pnl - fees) / risk


# ------------------------------------------------------------------ one coin

def run_coin(args):
    coin, start, end, shifts = args
    t0 = time.time()
    a = S.load5m(coin)
    lv = S.Levels(a)
    atr5 = S.rma(S.true_range(a), 144)
    rsi = wilder_rsi(a["c"])
    side = {1: Side(a, 1, atr5, rsi), -1: Side(a, -1, atr5, rsi)}
    bar_of = {int(t): k for k, t in enumerate(a["t"][(a["t"] >= start - 3600) & (a["t"] < end + 3600)])}
    base = int(np.searchsorted(a["t"], start - 3600))
    hours = range(start, end, 3600)
    results = {}
    for shift in shifts:
        for touches in TOUCHES:
            snaps = [lv.at(H, touches, shift) for H in hours]
            for width in WIDTHS:
                events = []                                       # (i, sign, zl, zh, strength, trend_ok, snap_idx)
                zcache = []
                for si, (H, sn) in enumerate(zip(hours, snaps)):
                    zs = S.zones(sn, width)
                    zcache.append(zs)
                    i0 = bar_of.get(H)
                    if i0 is None:
                        continue
                    i0 += base
                    px, em = sn["px"], sn["ema"]
                    hh, ll = a["h"][i0:i0 + 12], a["l"][i0:i0 + 12]
                    for zl, zh, st in zs:
                        if zh < px:
                            hit = np.flatnonzero(ll <= zh)
                            if len(hit):
                                events.append((i0 + hit[0], 1, zl, zh, st, px > em, si))
                        elif zl > px:
                            hit = np.flatnonzero(hh >= zl)
                            if len(hit):
                                events.append((i0 + hit[0], -1, -zh, -zl, st, px < em, si))
                events.sort()
                for trig in TRIGGERS:
                    ent = []
                    for ev in events:
                        i, sg, zl, zh, st, tok, si = ev
                        r = trigger(side[sg], trig, i, zl, zh)
                        if r:
                            ent.append((ev, r))
                    for stp, tgt in itertools.product(STOPS, TARGETS):
                        trades = []
                        for ev, (j, e, fee, intra, low) in ent:
                            i, sg, zl, zh, st, tok, si = ev
                            sd = side[sg]
                            mlev = [sg * p for p, _ in snaps[si]["levels"]]
                            s = stop_price(sd, stp, j, e, zl, mlev)
                            if s is None:
                                continue
                            zab = [sg * (z[0] if sg > 0 else z[1]) for z in zcache[si]]
                            tps = targets(sd, tgt, i, e, e - s, low, zab)
                            x = exit_trade(sd, j, e, s, tps, fee, intra)
                            if x is None:
                                continue
                            trades.append((j, x[0], x[1], sg, st, tok))
                        results[(shift, touches, width, trig, stp, tgt)] = np.array(
                            trades, dtype=[("j", "i8"), ("x", "i8"), ("R", "f8"), ("sg", "i1"), ("st", "i2"), ("tok", "?")])
    OUT.mkdir(exist_ok=True)
    tag = ("_placebo" if shifts != (0.0,) else "") + ("_nofees" if os.environ.get("NOFEES") else "")
    with open(OUT / f"{coin}{tag}.pkl", "wb") as f:
        pickle.dump({"coin": coin, "t": a["t"], "start": start, "end": end, "results": results}, f)
    return coin, round(time.time() - t0), len(results)


def select(tr, strength, trend):
    """One position per coin at a time: candidate trades in entry order, skipping overlaps."""
    m = tr["st"] >= strength
    if trend == "ema200":
        m &= tr["tok"]
    tr = tr[m]
    tr = tr[np.argsort(tr["j"], kind="stable")]
    keep, busy = [], -1
    for k in range(len(tr)):
        if tr["j"][k] > busy:
            keep.append(k)
            busy = tr["x"][k]
    return tr[keep]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        pilot = "--pilot" in sys.argv
        coins = ["BTC"] if pilot else COINS
        s, e = (day("2024-01-01"), day("2024-04-01")) if pilot else (START, END)
        shifts = (0.0,)
        if "--placebo" in sys.argv:
            shifts = (0.008, -0.008)
        with mp.Pool(min(6, len(coins))) as pool:
            for r in pool.imap_unordered(run_coin, [(c, s, e, shifts) for c in coins]):
                print(*r, flush=True)
