"""Order-flow signals with limit-order entries at the maker fee (frozen plan: docs/order-flow-test.md, second part).

Run:  FLOW_ENTRY=5m .venv/bin/python backtest/flow_limit_bt.py run [--placebo]
      FLOW_ENTRY=5m .venv/bin/python backtest/flow_limit_bt.py report
Signals, levels, stops, targets and exits come from flow_bt.py and level_bt.py; only the entry differs.
"""
import itertools
import multiprocessing as mp
import pickle
import sys
import time

import numpy as np
import pandas as pd

import flow_bt as FB
import level_bt as B
import snapshots as S

ENTRY, SEC = FB.ENTRY, FB.SEC
VALID = {"1m": 10, "5m": 6, "15m": 4}[ENTRY]        # candles a limit order stays in the book
PLACES = ("close", "zone")
DT = [("j", "i8"), ("x", "i8"), ("R", "f8"), ("sg", "i1"), ("st", "i2"), ("tok", "?"), ("risk", "f8")]


def fill(sd, j, limit):
    """Bar that fills a resting buy limit placed after bar j: price must trade through it."""
    edge = limit - B.THROUGH * abs(limit)
    hit = np.flatnonzero(sd.L[j + 1:min(j + 1 + VALID, sd.n - 1)] <= edge)
    return j + 1 + hit[0] if len(hit) else None


def run_coin(args):
    coin, start, end, shifts = args
    t0 = time.time()
    a, delta, z, a5, f1 = FB.load(coin)
    lv = FB.Levels(a5)
    side = FB.sides(a, delta, z)
    f = pd.read_feather(S.DATA / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    fund = (f.date.values.astype("datetime64[s]").astype(np.int64), np.r_[0.0, np.cumsum(f.funding_rate.values)])
    bph, n1 = 3600 // SEC, SEC // 60
    hours = range(start, end, 3600)
    pos = np.searchsorted(a["t"], np.array(hours))

    def touch1(i, sg, edge):
        if n1 == 1:
            return i
        k0 = int(np.searchsorted(f1["t"], a["t"][i]))
        seg = f1["l"][k0:k0 + n1] <= edge if sg > 0 else f1["h"][k0:k0 + n1] >= edge
        h = np.flatnonzero(seg)
        return k0 + (h[0] if len(h) else n1)

    results, fills = {}, {}
    for shift, plus in itertools.product(shifts, FB.PLUS):
        snaps = [lv.at(H, FB.TOUCHES, shift, plus) for H in hours]
        for width in FB.WIDTHS:
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
            for trig in FB.TRIGGERS:
                sigs = [(ev, r) for ev in events if (r := FB.trigger(side[ev[3]], trig, ev[0], ev[4], ev[5]))]
                for place in PLACES:
                    ent = []
                    for ev, (j, c, _, _, low) in sigs:
                        sd = side[ev[3]]
                        limit = c if place == "close" else min(c, ev[5])
                        k = fill(sd, j, limit)
                        if k is not None:
                            ent.append((ev, k, limit, min(low, sd.L[j:k + 1].min())))
                    fills[(shift, plus, width, trig, place)] = (len(sigs), len(ent))
                    for stp, tgt in itertools.product(FB.STOPS, FB.TARGETS):
                        trades = []
                        for ev, k, e, low in ent:
                            i, _, _, sg, zl, zh, st, tok, si = ev
                            sd = side[sg]
                            s = B.stop_price(sd, stp, k, e, zl, [sg * p for p, _ in snaps[si]["levels"]])
                            if s is None:
                                continue
                            zab = [sg * (q[0] if sg > 0 else q[1]) for q in zcache[si]]
                            tps = B.targets(sd, tgt, i, e, e - s, low, zab)
                            x = B.exit_trade(sd, k, e, s, tps, B.MAKER, True)
                            if x is None:
                                continue
                            R = x[1] - B.funding_cost(fund, a["t"][k] + SEC, a["t"][x[0]] + SEC, sg, e) / (e - s)
                            trades.append((k, x[0], R, sg, st, tok, (e - s) / abs(e)))
                        results[(shift, plus, width, trig, stp, tgt, place)] = np.array(trades, dtype=DT)
    with open(B.OUT / f"flowlim_{coin}_{ENTRY}{'_placebo' if shifts != (0.0,) else ''}.pkl", "wb") as fh:
        pickle.dump({"coin": coin, "t": a["t"], "results": results, "fills": fills}, fh)
    return coin, ENTRY, round(time.time() - t0), len(results)


def report():
    ld = lambda sfx: {c: pickle.load(open(B.OUT / f"flowlim_{c}_{ENTRY}{sfx}.pkl", "rb")) for c in B.COINS}
    runs, plc = ld(""), ld("_placebo")
    real = FB.table(runs)                                # key: (plus, width, trig, stop, target, place, strength, trend)
    pl = [FB.table(plc, s) for s in (0.008, -0.008)]
    plm = lambda k, p: np.nanmean([t[k][p]["avg"] for t in pl])
    days = (B.SPLIT - B.START) / 86400

    def rate(k):
        s = [r["fills"][(0.0, k[0], k[1], k[2], k[5])] for r in runs.values()]
        return sum(x[1] for x in s) / max(1, sum(x[0] for x in s))
    line = lambda k: (f"  {k[2]:6} limit={k[5]:5} stop={k[3]:3} tgt={k[4]:6} trend={k[7]:6} w={str(k[1]):5} str>={k[6]} levels={'plus' if k[0] else 'base'}"
                      f" | train n={real[k]['train']['n']:5} ({real[k]['train']['n'] / days:.1f}/day) avg {real[k]['train']['avg']:+.3f}R win {real[k]['train']['win']:.0%}"
                      f" coins+ {real[k]['train_pos']} | placebo {plm(k, 'train'):+.3f} | held n={real[k]['hold']['n']} avg {real[k]['hold']['avg']:+.3f}"
                      f" coins+ {real[k]['hold_pos']} placebo {plm(k, 'hold'):+.3f} | filled {rate(k):.0%} | median stop {real[k]['risk']:.2%}")
    sel = [k for k in real if k[2] in FB.SELECTABLE]
    big = [k for k in sel if real[k]["train"]["n"] >= 30]
    print(f"=== LIMIT ENTRIES, {ENTRY}: {len(sel)} selectable variants")
    print(f"positive on training after fees: {np.mean([real[k]['train']['avg'] > 0 for k in big]):.1%} of {len(big)} with 30+ trades; "
          f"on held-back: {np.mean([real[k]['hold']['avg'] > 0 for k in big if real[k]['hold']['n'] >= 30]):.1%}")
    elig = sorted([k for k in sel if real[k]["train"]["n"] >= 150 and real[k]["train_pos"] >= 4], key=lambda k: -real[k]["train"]["avg"])
    print(f"passed the selection rule: {len(elig)}; of those positive on held-back: {sum(real[k]['hold']['avg'] > 0 for k in elig)}")
    for k in elig[:8]:
        print(line(k))
    if elig:
        k = elig[0]
        print("  selected, by coin (n, avg R) train:", {c: (v["n"], round(v["avg"], 3)) for c, v in real[k]["train_coin"].items()})
        print("                               held:", {c: (v["n"], round(v["avg"], 3)) for c, v in real[k]["hold_coin"].items()})
    print("best 5 on training (150+ trades), whether or not they passed:")
    for k in sorted([k for k in sel if real[k]["train"]["n"] >= 150], key=lambda k: -real[k]["train"]["avg"])[:5]:
        print(line(k))
    print("mean over variants with 30+ training trades (after fees / minus placebo / held-back / trades per variant):")
    for trig, place in itertools.product(FB.TRIGGERS, PLACES):
        ks = [k for k in real if k[2] == trig and k[5] == place and real[k]["train"]["n"] >= 30]
        if ks:
            print(f"  {trig:6} limit={place:5} {np.mean([real[k]['train']['avg'] for k in ks]):+.3f} / "
                  f"{np.nanmean([real[k]['train']['avg'] - plm(k, 'train') for k in ks]):+.4f} / "
                  f"{np.nanmean([real[k]['hold']['avg'] for k in ks]):+.3f} / {np.mean([real[k]['train']['n'] for k in ks]):.0f}")
    for opt, p in (("levels", 0), ("width", 1), ("stop", 3), ("target", 4), ("strength", 6), ("trend", 7)):
        g = {}
        for k in big:
            g.setdefault(str(k[p]), []).append(real[k]["train"]["avg"])
        print(f"  {opt}: " + ", ".join(f"{v} {np.mean(x):+.3f}" for v, x in g.items()))


if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "run") == "report":
        report()
    else:
        pilot = "--pilot" in sys.argv
        coins = ["BTC"] if pilot else B.COINS
        s, e = (B.day("2024-01-01"), B.day("2024-03-01")) if pilot else (B.START, B.END)
        shifts = (0.008, -0.008) if "--placebo" in sys.argv else (0.0,)
        with mp.Pool(min(6, len(coins))) as pool:
            for r in pool.imap_unordered(run_coin, [(c, s, e, shifts) for c in coins]):
                print(*r, flush=True)
