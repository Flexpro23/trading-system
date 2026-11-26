"""Scalp bot as it runs live, with and without a trend-direction filter (15m entries, flip trigger).

Rules copied from the live bot (ui/server.py s_enter, m_apply, m_runner, m_trail):
  entry   : zone touch, then a 15m candle closing back out of the zone with delta z >= 1.5 (flip), zones of strength 2+,
            market order at the close of the signal candle
  stop    : one ATR beyond the zone, at least 0.3% away; the signal is skipped if the stop is nearer than 0.8% or
            farther than 3%
  targets : a third closes at 1R and at 2R; the stop moves to entry after 2R; at 3R the last third becomes a runner
            with its stop at 2R, then 1R below each new whole R reached. No time limit (capped at 30 days here)
  overlap : a new trade is skipped if a trade on the same coin and side is open with an entry within 0.5%
  costs   : taker fee on entry, maker fee on targets, taker fee plus slippage on stops, funding paid or received

Filter tested (fixed before the first run): the swing bot's 20-day momentum rule on closed 4h candles.
  with trend : long when the 20-day return is above +5% and the close is above the 100-candle 4h average;
               short on the mirror image
  against    : the opposite side of that rule
  neutral    : neither (the rule is flat)
The filter keeps only "with trend" trades.

Pass rule (fixed before the first run): on the training period, the filtered trades must average above 0 R after costs,
beat the unfiltered average, and be positive on at least 4 of the 6 coins, for BOTH zone widths. Only then are the
held-back numbers read as a check: they must also average above 0 R. Nothing is tuned on the held-back period.

Limits: only the 6 coins with 1m taker-buy data (the live bot watches 58). Zones are the backtest's level map, not the
exact zones the desk draws. Stops fill at the stop price plus slippage; a gap through the stop fills at the next open.

Run:  .venv/bin/python backtest/scalp_trend_bt.py   (writes backtest/reports/scalp_trend_report.txt and .json)
"""
import json
import multiprocessing as mp
import os
import sys
import time

os.environ["FLOW_ENTRY"] = "15m"                     # flow_bt reads this at import
import numpy as np                                   # noqa: E402
import pandas as pd                                  # noqa: E402

import flow_bt as FB                                 # noqa: E402
import level_bt as B                                 # noqa: E402
import snapshots as S                                # noqa: E402

MIN_DIST, MAX_DIST, FLOOR = 0.008, 0.03, 0.003
HOLD = 30 * 96                                       # 30 days of 15m candles
WIDTHS = (0.003, "atr")
REPORTS = B.OUT.parent / "reports"


def exit_live(sd, j, e, s, fund, t, sg):
    """Long view. Returns (exit bar, R after costs, targets reached)."""
    risk, ref = e - s, abs(e)
    tps = [e + risk, e + 2 * risk, e + 3 * risk]
    stop, rem, pnl, fees, k = s, 1.0, 0.0, B.TAKER * ref, 0
    end = min(j + HOLD, sd.n - 1)

    def done(b, px, fee):
        nonlocal pnl, fees
        pnl += rem * (px - e)
        fees += rem * fee * abs(px)
        f = B.funding_cost(fund, t[j] + FB.SEC, t[b] + FB.SEC, sg, e)
        return b, (pnl - fees - f) / risk, k

    for b in range(j + 1, end + 1):
        if sd.L[b] <= stop:                          # stop first; a gap through it fills at the open
            return done(b, min(stop, sd.O[b]), B.TAKER + B.SLIP)
        if k < 3:
            while k < 3 and sd.H[b] >= tps[k]:
                if k < 2:
                    pnl += (tps[k] - e) / 3
                    fees += B.MAKER * abs(tps[k]) / 3
                    rem -= 1 / 3
                k += 1
                if k == 2:
                    stop = max(stop, e)
                if k == 3:
                    stop = e + 2 * risk              # the runner; no trailing in the candle that made it
        else:
            n = int((sd.H[b] - e) / risk + 1e-9) - 1
            if n >= 2:
                stop = max(stop, e + n * risk)
        if k and sd.C[b] < stop:                     # a stop raised inside a candle that already closed below it
            return done(b, sd.C[b], B.TAKER + B.SLIP)
    return done(end, sd.C[end], B.TAKER)


def trend_state(a5, t_close):
    """+1, -1 or 0 per signal close time, from the last 4h candle closed by then."""
    h4 = S.resample(a5, "4h")
    c = h4["c"]
    ret = np.full(len(c), np.nan)
    ret[120:] = c[120:] / c[:-120] - 1
    sma = pd.Series(c).rolling(100).mean().values
    st = np.where((ret > 0.05) & (c > sma), 1, np.where((ret < -0.05) & (c < sma), -1, 0))
    k = np.searchsorted(h4["t"] + 4 * 3600, t_close, side="right") - 1
    return np.where(k >= 0, st[np.maximum(k, 0)], 0)


def run_coin(coin):
    t0 = time.time()
    a, delta, z, a5, f1 = FB.load(coin)
    a.setdefault("o", a["c"])
    lv = FB.Levels(a5)
    side = FB.sides(a, delta, z)
    for sg in (1, -1):
        side[sg].O = sg * a["o"]
    f = pd.read_feather(S.DATA / f"{coin}_USDT_USDT-1h-funding_rate.feather")
    fund = (f.date.values.astype("datetime64[s]").astype(np.int64), np.r_[0.0, np.cumsum(f.funding_rate.values)])
    bph = 3600 // FB.SEC
    hours = range(B.START, B.END, 3600)
    pos = np.searchsorted(a["t"], np.array(hours))
    snaps = [lv.at(H, FB.TOUCHES, 0.0, False) for H in hours]
    out = {}
    for width in WIDTHS:
        events = []
        for si, (H, sn) in enumerate(zip(hours, snaps)):
            i0 = int(pos[si])
            if i0 >= len(a["t"]) or a["t"][i0] != H:
                continue
            px = sn["px"]
            hh, ll = a["h"][i0:i0 + bph], a["l"][i0:i0 + bph]
            for zl, zh, st in S.zones(sn, width):
                if st < 2:
                    continue
                if zh < px and len(hit := np.flatnonzero(ll <= zh)):
                    events.append((i0 + hit[0], 1, zl, zh))
                elif zl > px and len(hit := np.flatnonzero(hh >= zl)):
                    events.append((i0 + hit[0], -1, -zh, -zl))
        events.sort()
        cand, seen = [], set()
        for i, sg, zl, zh in events:
            r = FB.trigger(side[sg], "flip", i, zl, zh)
            if not r or (r[0], sg) in seen:          # live takes one signal per coin, side and candle
                continue
            seen.add((r[0], sg))
            j, e = r[0], r[1]
            sd = side[sg]
            s = min(zl - sd.atr[j - 1], e - FLOOR * abs(e))
            if not MIN_DIST <= (e - s) / abs(e) <= MAX_DIST:
                continue
            cand.append((j, sg, e, s))
        cand.sort()
        trend = trend_state(a5, a["t"][[c[0] for c in cand]] + FB.SEC) if cand else []
        trades = []
        for (j, sg, e, s), tr in zip(cand, trend):
            x, R, k = exit_live(side[sg], j, e, s, fund, a["t"], sg)
            trades.append((int(a["t"][j]), j, x, sg, float(R), k, int(tr * sg), abs(e)))
        out[str(width)] = trades
    return coin, out, round(time.time() - t0)


def overlap(trades, keep):
    """Live overlap rule on the kept trades: skip a trade if the same side is open within 0.5% of its entry."""
    kept, open_ = [], []
    for tr in sorted(trades, key=lambda x: x[1]):
        if not keep(tr):
            continue
        t, j, x, sg, R, k, ts, e = tr
        open_ = [o for o in open_ if o[2] > j]
        if any(o[3] == sg and abs(o[7] - e) / e < 0.005 for o in open_):
            continue
        open_.append(tr)
        kept.append(tr)
    return kept


def stats(rows):
    R = np.array([r[4] for r in rows])
    if not len(R):
        return {"n": 0}
    w, l = R[R > 0].sum(), -R[R <= 0].sum()
    return {"n": int(len(R)), "avg_R": round(float(R.mean()), 3), "total_R": round(float(R.sum()), 1),
            "win_pct": round(100 * float((R > 0).mean()), 1), "pf": round(float(w / l), 2) if l else None,
            "longs_avg_R": round(float(R[[r[3] > 0 for r in rows]].mean()), 3) if any(r[3] > 0 for r in rows) else None,
            "shorts_avg_R": round(float(R[[r[3] < 0 for r in rows]].mean()), 3) if any(r[3] < 0 for r in rows) else None,
            "reached": {f"tp{k}": int(sum(r[5] >= k for r in rows)) for k in (1, 2, 3)}}


def main():
    with mp.Pool(6) as pool:
        res = {}
        for coin, out, sec in pool.imap_unordered(run_coin, B.COINS):
            res[coin] = out
            print(coin, f"{sec}s", {w: len(v) for w, v in out.items()}, flush=True)
    groups = {"all": lambda r: True, "with trend": lambda r: r[6] == 1,
              "against trend": lambda r: r[6] == -1, "neutral": lambda r: r[6] == 0}
    report, lines = {}, []
    for part, sel in (("train", lambda t: t < B.SPLIT), ("held back", lambda t: t >= B.SPLIT)):
        for width in map(str, WIDTHS):
            for g, keep in groups.items():
                per = {c: overlap([r for r in res[c][width] if sel(r[0])], keep) for c in B.COINS}
                st = stats([r for v in per.values() for r in v])
                st["coins_positive"] = sum(1 for v in per.values() if v and np.mean([r[4] for r in v]) > 0)
                st["by_coin"] = {c: stats(v).get("avg_R") for c, v in per.items()}
                report[f"{part} | width {width} | {g}"] = st
    verdict = []
    for width in map(str, WIDTHS):
        a, f = report[f"train | width {width} | all"], report[f"train | width {width} | with trend"]
        ok = f["n"] and f["avg_R"] > 0 and f["avg_R"] > a["avg_R"] and f["coins_positive"] >= 4
        verdict.append(ok)
    passed = all(verdict)
    held = [report[f"held back | width {w} | with trend"].get("avg_R", -1) > 0 for w in map(str, WIDTHS)] if passed else None
    report["verdict"] = {"training_pass_by_width": dict(zip(map(str, WIDTHS), verdict)), "training_pass": passed,
                         "held_back_positive": held}
    for k, v in report.items():
        if k == "verdict":
            continue
        lines.append(f"{k:42} n={v['n']:5}" + (f"  avg {v['avg_R']:+.3f} R  total {v['total_R']:+8.1f} R  win {v['win_pct']:4.1f}%"
                     f"  PF {v['pf']}  longs {v['longs_avg_R']}  shorts {v['shorts_avg_R']}  coins+ {v['coins_positive']}/6"
                     f"  reached {v['reached']}\n{'':44}by coin {v['by_coin']}" if v["n"] else ""))
    lines.append(f"verdict: {report['verdict']}")
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "scalp_trend_report.txt").write_text("\n".join(lines) + "\n")
    (REPORTS / "scalp_trend_report.json").write_text(json.dumps(report, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
