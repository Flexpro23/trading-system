"""Order-flow test: zone touch plus delta, on 1m, 5m or 15m entries (frozen plan: docs/order-flow-test.md).

Run:  FLOW_ENTRY=5m .venv/bin/python backtest/flow_bt.py run [--placebo]   (NOFEES=1 for the before-fee run)
      FLOW_ENTRY=5m .venv/bin/python backtest/flow_bt.py report
Stops, targets and exits are the ones in level_bt.py. Shorts are longs on mirrored prices, with delta negated.
"""
import itertools
import multiprocessing as mp
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

os.environ.pop("ENTRY", None)                       # level_bt reads ENTRY; this file sets its own settings below
import flow_data as F                                # noqa: E402
import level_bt as B                                 # noqa: E402
import snapshots as S                                # noqa: E402

ENTRY = os.environ.get("FLOW_ENTRY", "5m")
SEC = {"1m": 60, "5m": 300, "15m": 900}[ENTRY]
B.WAIT, B.HOLD, B.MIN_STOP, B.PCT_STOP = {"1m": (60, 240, 0.0015, 0.003),
                                          "5m": (36, 288, 0.002, 0.005),
                                          "15m": (24, 240, 0.003, 0.008)}[ENTRY]
TRIGGERS = ("ddiv", "absorb", "flip", "close")      # "close" is the reference with no delta condition
SELECTABLE = ("ddiv", "absorb", "flip")
STOPS, TARGETS, WIDTHS, STRENGTHS, TRENDS, PLUS = ("atr", "pct"), ("r", "levels"), (0.003, "atr"), (1, 2), ("none", "ema200"), (False, True)
TOUCHES, ZT, ZWIN, NEAR_PLUS = 3, 1.5, 288, 0.02
NOFEES = bool(os.environ.get("NOFEES"))


class Levels(S.Levels):
    """The base level map, plus (on request) untouched 1h and 30m opens near the price."""

    def __init__(self, a):
        super().__init__(a)
        self.extra = [S.open_events(self.x["1h"], "1h"), S.open_events(S.resample(a, "30m"), "30m")]

    def at(self, H, touches, shift=0.0, plus=False):
        sn = super().at(H, touches, shift)
        if plus:
            px = sn["px"]
            for ev in self.extra:
                m = (ev["born"] <= H) & (np.abs(ev["price"] - px) <= NEAR_PLUS * px)
                m[m] = (ev["visits"][m] <= H).sum(axis=1) < touches
                sn["levels"] += [(p * (1 + shift), "open_" + ev["tf"]) for p in ev["price"][m]]
        return sn


class Side(B.Side):
    def __init__(self, a, sign, atr, rsi, delta, z):
        super().__init__(a, sign, atr, rsi)
        self.Z = sign * z
        self.CV = sign * np.cumsum(delta)


def trigger(sd, kind, i, zl, zh):
    """Entry after a touch of zone [zl, zh] at bar i (long view). Market order at the close of the signal bar."""
    H, L, C, n = sd.H, sd.L, sd.C, sd.n
    ref = abs(zh)
    cancel = zl - sd.atr[i - 1]
    tol = max(0.0025 * ref, zh - zl)
    for j in range(i, min(i + B.WAIT, n - 1)):
        if C[j] < cancel:
            return None
        if kind == "close":
            ok = C[j] > zh
        elif kind == "flip":
            ok = C[j] > zh and sd.Z[j] >= ZT
        elif kind == "absorb":
            ok = L[j] <= zh and sd.Z[j] <= -ZT and C[j] >= (H[j] + L[j]) / 2 and C[j] > zl
        else:
            k = j - 2                                   # a pivot low is known two bars later
            ok = False
            if k >= i and sd.PL[k] and L[k] <= zh + tol:
                p = next((p for p in range(k - 4, max(0, k - 40) - 1, -1) if sd.PL[p] and L[p] > L[k]), None)
                ok = p is not None and sd.CV[k] > sd.CV[p]
        if ok:
            return (j, C[j], B.TAKER, False, L[i:j + 1].min())
    return None


def load(coin):
    """Entry candles, their delta, the 5m candles the levels are built from, and the 1m candles."""
    f1 = F.load(coin)
    d1 = 2 * f1["tb"] - f1["v"]
    a5 = S.load5m(coin)
    if ENTRY == "1m":
        a, delta = {k: f1[k] for k in ("t", "o", "h", "l", "c", "v")}, d1
    else:
        a = a5 if ENTRY == "5m" else {k: v for k, v in S.resample(a5, ENTRY).items() if k != "tc"}
        tb = f1["t"] // SEC * SEC
        idx = np.minimum(np.searchsorted(a["t"], tb), len(a["t"]) - 1)
        ok = a["t"][idx] == tb
        delta = np.zeros(len(a["t"]))
        np.add.at(delta, idx[ok], d1[ok])
    sd = pd.Series(delta).rolling(ZWIN).std().shift(1).values
    z = np.where(sd > 0, delta / np.where(sd > 0, sd, 1), 0.0)
    return a, delta, np.nan_to_num(z), a5, f1


def sides(a, delta, z):
    atr, rsi = S.rma(S.true_range(a), 144), B.wilder_rsi(a["c"])
    return {1: Side(a, 1, atr, rsi, delta, z), -1: Side(a, -1, atr, rsi, delta, z)}


def run_coin(args):
    coin, start, end, shifts = args
    t0 = time.time()
    a, delta, z, a5, f1 = load(coin)
    lv = Levels(a5)
    side = sides(a, delta, z)
    f = pd.read_feather(S.DATA / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    fund = (f.date.values.astype("datetime64[s]").astype(np.int64), np.r_[0.0, np.cumsum(f.funding_rate.values)])
    bph, n1 = 3600 // SEC, SEC // 60
    hours = range(start, end, 3600)
    pos = np.searchsorted(a["t"], np.array(hours))

    def touch1(i, sg, edge):
        """1m bar of the first touch inside entry candle i: a real order fills the first zone price reaches."""
        if n1 == 1:
            return i
        k0 = int(np.searchsorted(f1["t"], a["t"][i]))
        seg = f1["l"][k0:k0 + n1] <= edge if sg > 0 else f1["h"][k0:k0 + n1] >= edge
        h = np.flatnonzero(seg)
        return k0 + (h[0] if len(h) else n1)

    results = {}
    for shift, plus in itertools.product(shifts, PLUS):
        snaps = [lv.at(H, TOUCHES, shift, plus) for H in hours]
        for width in WIDTHS:
            events, zcache = [], []
            for si, (H, sn) in enumerate(zip(hours, snaps)):
                zs = S.zones(sn, width)
                zcache.append(zs)
                i0 = int(pos[si])
                if i0 >= len(a["t"]) or a["t"][i0] != H:
                    continue
                px, em = sn["px"], sn["ema"]
                hh, ll = a["h"][i0:i0 + bph], a["l"][i0:i0 + bph]
                for zl, zh, st in zs:
                    if zh < px:
                        hit = np.flatnonzero(ll <= zh)
                        if len(hit):
                            i = i0 + hit[0]
                            events.append((i, touch1(i, 1, zh), px - zh, 1, zl, zh, st, px > em, si))
                    elif zl > px:
                        hit = np.flatnonzero(hh >= zl)
                        if len(hit):
                            i = i0 + hit[0]
                            events.append((i, touch1(i, -1, zl), zl - px, -1, -zh, -zl, st, px < em, si))
            events.sort()
            for trig in TRIGGERS:
                ent = [(ev, r) for ev in events if (r := trigger(side[ev[3]], trig, ev[0], ev[4], ev[5]))]
                for stp, tgt in itertools.product(STOPS, TARGETS):
                    trades = []
                    for ev, (j, e, fee, intra, low) in ent:
                        i, _, _, sg, zl, zh, st, tok, si = ev
                        sd = side[sg]
                        s = B.stop_price(sd, stp, j, e, zl, [sg * p for p, _ in snaps[si]["levels"]])
                        if s is None:
                            continue
                        zab = [sg * (q[0] if sg > 0 else q[1]) for q in zcache[si]]
                        tps = B.targets(sd, tgt, i, e, e - s, low, zab)
                        x = B.exit_trade(sd, j, e, s, tps, fee, intra)
                        if x is None:
                            continue
                        R = x[1] - (0 if NOFEES else B.funding_cost(fund, a["t"][j] + SEC, a["t"][x[0]] + SEC, sg, e) / (e - s))
                        trades.append((j, x[0], R, sg, st, tok, (e - s) / abs(e)))
                    results[(shift, plus, width, trig, stp, tgt)] = np.array(
                        trades, dtype=[("j", "i8"), ("x", "i8"), ("R", "f8"), ("sg", "i1"), ("st", "i2"), ("tok", "?"), ("risk", "f8")])
    with open(B.OUT / f"flow_{coin}_{ENTRY}{tag(shifts)}.pkl", "wb") as fh:
        pickle.dump({"coin": coin, "t": a["t"], "results": results}, fh)
    return coin, ENTRY, round(time.time() - t0), len(results)


def tag(shifts=(0.0,)):
    return ("_placebo" if shifts != (0.0,) else "") + ("_nofees" if NOFEES else "")


# ------------------------------------------------------------------ report

def stats(R):
    R = np.asarray(R)
    if not len(R):
        return {"n": 0, "avg": np.nan, "win": np.nan}
    return {"n": len(R), "avg": R.mean(), "win": (R > 0).mean()}


def table(runs, shift=0.0):
    rows = {}
    for key in runs["BTC"]["results"]:
        if key[0] != shift:
            continue
        for st, tr in itertools.product(STRENGTHS, TRENDS):
            per = {}
            for c, run in runs.items():
                t = B.select(run["results"][key], st, tr)
                per[c] = (run["t"][t["j"]], t["R"], t["risk"])
            row = {}
            for part, sel in (("train", lambda ti: ti < B.SPLIT), ("hold", lambda ti: ti >= B.SPLIT)):
                row[part] = stats(np.concatenate([R[sel(ti)] for ti, R, _ in per.values()]))
                row[part + "_pos"] = sum(stats(R[sel(ti)])["avg"] > 0 for ti, R, _ in per.values())
                row[part + "_coin"] = {c: stats(R[sel(ti)]) for c, (ti, R, _) in per.items()}
            row["risk"] = float(np.median(np.concatenate([k for _, _, k in per.values()]))) if row["train"]["n"] else np.nan
            rows[key[1:] + (st, tr)] = row
    return rows


def report():
    ld = lambda sfx: {c: pickle.load(open(B.OUT / f"flow_{c}_{ENTRY}{sfx}.pkl", "rb")) for c in B.COINS}
    real, nof = table(ld("")), table(ld("_nofees"))
    plc = ld("_placebo")
    pl = [table(plc, s) for s in (0.008, -0.008)]
    plm = lambda k, p: np.nanmean([t[k][p]["avg"] for t in pl])
    name = lambda k: (f"{k[2]:6} stop={k[3]:3} tgt={k[4]:6} trend={k[6]:6} w={str(k[1]):5} str>={k[5]} "
                      f"levels={'plus' if k[0] else 'base'}")
    days = {"train": (B.SPLIT - B.START) / 86400, "hold": (B.END - B.SPLIT) / 86400}
    line = lambda k: (f"  {name(k)} | train n={real[k]['train']['n']:5} ({real[k]['train']['n'] / days['train']:.1f}/day) "
                      f"avg {real[k]['train']['avg']:+.3f}R win {real[k]['train']['win']:.0%} coins+ {real[k]['train_pos']} "
                      f"| before fees {nof[k]['train']['avg']:+.3f} | placebo {plm(k, 'train'):+.3f} "
                      f"| held n={real[k]['hold']['n']} avg {real[k]['hold']['avg']:+.3f} placebo {plm(k, 'hold'):+.3f} | median stop {real[k]['risk']:.2%}")
    sel = [k for k in real if k[2] in SELECTABLE]
    big = [k for k in sel if real[k]["train"]["n"] >= 30]
    print(f"=== ENTRY {ENTRY}: {len(sel)} selectable variants")
    print(f"positive on training after fees: {np.mean([real[k]['train']['avg'] > 0 for k in big]):.1%} of {len(big)} with 30+ trades; "
          f"before fees: {np.mean([nof[k]['train']['avg'] > 0 for k in big]):.1%}")
    elig = sorted([k for k in sel if real[k]["train"]["n"] >= 150 and real[k]["train_pos"] >= 4], key=lambda k: -real[k]["train"]["avg"])
    print(f"passed the selection rule: {len(elig)}")
    for k in elig[:5]:
        print(line(k))
    if elig:
        print("  selected, by coin:", {c: (v["n"], round(v["avg"], 3)) for c, v in real[elig[0]]["train_coin"].items()},
              "held:", {c: (v["n"], round(v["avg"], 3)) for c, v in real[elig[0]]["hold_coin"].items()})
    print("best 5 before fees (150+ training trades):")
    for k in sorted([k for k in sel if real[k]["train"]["n"] >= 150], key=lambda k: -nof[k]["train"]["avg"])[:5]:
        print(line(k))
    print("by entry type, mean over variants with 30+ training trades (after fees / before fees / minus placebo / trades per variant):")
    for trig in TRIGGERS:
        ks = [k for k in real if k[2] == trig and real[k]["train"]["n"] >= 30]
        if ks:
            print(f"  {trig:6} {np.mean([real[k]['train']['avg'] for k in ks]):+.3f} / {np.mean([nof[k]['train']['avg'] for k in ks]):+.3f} / "
                  f"{np.nanmean([real[k]['train']['avg'] - plm(k, 'train') for k in ks]):+.4f} / {np.mean([real[k]['train']['n'] for k in ks]):.0f}")
    for opt, pos in (("levels", 0), ("width", 1), ("stop", 3), ("target", 4), ("strength", 5), ("trend", 6)):
        g = {}
        for k in big:
            g.setdefault(str(k[pos]), []).append(real[k]["train"]["avg"])
        print(f"  {opt}: " + ", ".join(f"{v} {np.mean(x):+.3f}" for v, x in g.items()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "report":
        report()
    else:
        pilot = "--pilot" in sys.argv
        coins = ["BTC"] if pilot else B.COINS
        s, e = (B.day("2024-01-01"), B.day("2024-03-01")) if pilot else (B.START, B.END)
        shifts = (0.008, -0.008) if "--placebo" in sys.argv else (0.0,)
        with mp.Pool(min(6, len(coins))) as pool:
            for r in pool.imap_unordered(run_coin, [(c, s, e, shifts) for c in coins]):
                print(*r, flush=True)
