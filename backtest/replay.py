"""Intrabar check for the selected swing variant: rebuild its 1h trades with full details, then replay the
same orders (same zone, stop and targets) on 5m candles. If the 5m path tells a different story than the
1h candle, the 1h result is not trustworthy. Run with ENTRY=1h.
Usage: replay.py <touches> <width> <trigger> <stop> <target> <strength> <trend> [shift]"""
import multiprocessing as mp
import sys

import numpy as np
import pandas as pd

import level_bt as B
import snapshots as S

touches, width, trig, stp, tgt, strength, trend = sys.argv[1:8]
touches, strength = int(touches), int(strength)
width = width if width == "atr" else float(width)
shift = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0


def coin_trades(coin):
    a5 = S.load5m(coin)
    lv = S.Levels(a5)
    x = S.resample(a5, "1h")
    a = {k: x[k] for k in ("t", "o", "h", "l", "c", "v")}
    side = {1: B.Side(a, 1, S.rma(S.true_range(a), 144), B.wilder_rsi(a["c"])),
            -1: B.Side(a, -1, S.rma(S.true_range(a), 144), B.wilder_rsi(a["c"]))}
    s5 = {1: B.Side(a5, 1, S.rma(S.true_range(a5), 144), B.wilder_rsi(a5["c"])),
          -1: B.Side(a5, -1, S.rma(S.true_range(a5), 144), B.wilder_rsi(a5["c"]))}
    f = pd.read_feather(S.DATA / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    fund = (f.date.values.astype("datetime64[s]").astype(np.int64), np.r_[0.0, np.cumsum(f.funding_rate.values)])
    cand = []
    for H in range(B.START, B.END, 3600):
        i = int(np.searchsorted(a["t"], H))
        if i >= len(a["t"]) or a["t"][i] != H:
            continue
        sn = lv.at(H, touches, shift)
        zs = S.zones(sn, width)
        for zl, zh, st in zs:
            if st < strength:
                continue
            if zh < sn["px"]:
                sg, ok = 1, a["l"][i] <= zh
            elif zl > sn["px"]:
                sg, ok = -1, a["h"][i] >= zl
            else:
                continue
            if not ok or (trend == "ema200" and not ((sn["px"] > sn["ema"]) if sg > 0 else (sn["px"] < sn["ema"]))):
                continue
            mzl, mzh = (zl, zh) if sg > 0 else (-zh, -zl)
            sd = side[sg]
            r = B.trigger(sd, trig, i, mzl, mzh)
            if not r:
                continue
            j, e, fee, intra, low = r
            s = B.stop_price(sd, stp, j, e, mzl, [sg * p for p, _ in sn["levels"]])
            if s is None:
                continue
            tps = B.targets(sd, tgt, i, e, e - s, low, [sg * (z[0] if sg > 0 else z[1]) for z in zs])
            B.HOLD = 240
            xx = B.exit_trade(sd, j, e, s, tps, fee, intra)
            if xx is None:
                continue
            R1 = xx[1] - B.funding_cost(fund, a["t"][j], a["t"][xx[0]] + 3600, sg, e) / (e - s)
            k0 = int(np.searchsorted(a5["t"], a["t"][i]))
            seg = a5["l"][k0:k0 + 12] <= zh if sg > 0 else a5["h"][k0:k0 + 12] >= zl
            h = np.flatnonzero(seg)
            t5 = k0 + (h[0] if len(h) else 12)
            cand.append((j, xx[0], R1, sg, mzh, e, s, tps, fee, t5, (sn["px"] - zh) if sg > 0 else (zl - sn["px"])))
    cand.sort(key=lambda c: (c[0], c[9], c[10]))
    out, busy = [], -1
    for c in cand:
        if c[0] <= busy:
            continue
        busy = c[1]
        j, xj, R1, sg, mzh, e, s, tps, fee = c[:9]
        sd = s5[sg]
        k0 = int(np.searchsorted(a5["t"], a["t"][j]))
        ks = [k for k in range(k0, k0 + 12) if sd.L[k] <= mzh - B.THROUGH * abs(mzh)] if trig == "first" else []
        if trig == "first":
            if not ks:
                out.append((a["t"][j], R1, np.nan, sg))           # 1h said filled, 5m says not: should not happen
                continue
            k = ks[0]
            e5 = min(sd.O[k], mzh)
            intra = True
        else:
            k, e5, intra = k0 + 11, e, False                       # entered at the 1h close
        if e5 <= s:
            out.append((a["t"][j], R1, -1.0, sg))
            continue
        B.HOLD = 2880
        y = B.exit_trade(sd, k, e5, s, [t for t in tps], fee, intra)
        R5 = y[1] - B.funding_cost(fund, a5["t"][k], a5["t"][y[0]] + 300, sg, e5) / (e5 - s)
        out.append((a["t"][j], R1, R5, sg))
    return coin, out


if __name__ == "__main__":
    with mp.Pool(6) as pool:
        res = dict(pool.map(coin_trades, B.COINS))
    for part, sel in (("train", lambda t: t < B.SPLIT), ("held", lambda t: t >= B.SPLIT)):
        r1, r5 = [], []
        for coin, tr in res.items():
            a = np.array([(R1, R5) for t, R1, R5, sg in tr if sel(t)])
            if len(a):
                r1.append(a[:, 0]); r5.append(a[:, 1])
                print(f"  {coin} {part}: n={len(a)} 1h candles {a[:, 0].mean():+.3f}  5m replay {np.nanmean(a[:, 1]):+.3f}")
        r1, r5 = np.concatenate(r1), np.concatenate(r5)
        print(f"ALL {part}: n={len(r1)} avg R on 1h candles {r1.mean():+.3f} | same orders on 5m {np.nanmean(r5):+.3f} "
              f"| win {np.mean(r1 > 0):.0%} vs {np.nanmean(r5 > 0):.0%} | not filled on 5m {np.isnan(r5).sum()}")
