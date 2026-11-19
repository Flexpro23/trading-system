"""Results of level_bt.py: selection on training data by the frozen rule, then the held-back period."""
import itertools
import json
import pickle
import sys
from collections import defaultdict

import numpy as np

import level_bt as B

suffix = sys.argv[1] if len(sys.argv) > 1 else ""
runs = {c: pickle.load(open(B.OUT / f"{c}{suffix}.pkl", "rb")) for c in B.COINS}


def stats(R):
    R = np.asarray(R)
    if not len(R):
        return {"n": 0, "avg": np.nan, "win": np.nan, "pf": np.nan, "total": 0.0}
    w, l = R[R > 0].sum(), -R[R < 0].sum()
    return {"n": len(R), "avg": R.mean(), "win": (R > 0).mean(), "pf": w / l if l else np.inf, "total": R.sum()}


def variant_trades(key, strength, trend):
    out = {}
    for c, run in runs.items():
        tr = B.select(run["results"][key], strength, trend)
        t = run["t"]
        out[c] = {"t_in": t[tr["j"]], "t_out": t[tr["x"]] + {"5m": 300, "15m": 900, "1h": 3600}[B.ENTRY], "R": tr["R"], "sg": tr["sg"]}
    return out


rows = []
for key in runs["BTC"]["results"]:
    for strength, trend in itertools.product(B.STRENGTHS, B.TRENDS):
        tr = variant_trades(key, strength, trend)
        row = {"shift": key[0], "touches": key[1], "width": str(key[2]), "trigger": key[3], "stop": key[4],
               "target": key[5], "strength": strength, "trend": trend}
        for part, sel in (("train", lambda d: d["t_in"] < B.SPLIT), ("hold", lambda d: d["t_in"] >= B.SPLIT)):
            allR = np.concatenate([d["R"][sel(d)] for d in tr.values()])
            s = stats(allR)
            row.update({f"{part}_{k}": v for k, v in s.items()})
            row[f"{part}_coins_pos"] = sum(stats(d["R"][sel(d)])["avg"] > 0 for d in tr.values())
        rows.append(row)

elig = [r for r in rows if r["train_n"] >= 150 and r["train_coins_pos"] >= 4]
elig.sort(key=lambda r: -r["train_avg"])
best = elig[0] if elig else None

out = {"variants": len(rows), "eligible": len(elig), "best": best, "top10": elig[:10],
       "share_train_pos": float(np.mean([r["train_avg"] > 0 for r in rows if r["train_n"] >= 30])),
       "share_hold_pos": float(np.mean([r["hold_avg"] > 0 for r in rows if r["hold_n"] >= 30])),
       "share_elig_hold_pos": float(np.mean([r["hold_avg"] > 0 for r in elig])) if elig else None}
effects = {}
for opt in ("trigger", "stop", "target", "trend", "width", "strength", "touches"):
    g = defaultdict(lambda: [[], []])
    for r in rows:
        if r["train_n"] >= 30:
            g[r[opt]][0].append(r["train_avg"])
        if r["hold_n"] >= 30:
            g[r[opt]][1].append(r["hold_avg"])
    effects[opt] = {str(k): [float(np.mean(v[0])) if v[0] else None, float(np.mean(v[1])) if v[1] else None] for k, v in g.items()}
out["effects"] = effects

if best:
    key = (best["shift"], best["touches"], float(best["width"]) if best["width"] != "atr" else "atr",
           best["trigger"], best["stop"], best["target"])
    tr = variant_trades(key, best["strength"], best["trend"])
    out["best_by_coin"] = {c: {p: stats(d["R"][(d["t_in"] < B.SPLIT) == (p == "train")]) for p in ("train", "hold")}
                           for c, d in tr.items()}
    ev = []                                              # account level: all coins in time order
    for c, d in tr.items():
        for a, b, R, sg in zip(d["t_in"], d["t_out"], d["R"], d["sg"]):
            ev.append((a, b, R, sg, c))
    ev.sort()
    for part, sel in (("train", lambda e: e[0] < B.SPLIT), ("hold", lambda e: e[0] >= B.SPLIT)):
        es = [e for e in ev if sel(e)]
        closes = sorted((b, R) for a, b, R, sg, c in es)
        eq = np.cumsum([R for _, R in closes]) if closes else np.array([0.0])
        dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
        streak = cur = 0
        for _, R in closes:
            cur = cur + 1 if R < 0 else 0
            streak = max(streak, cur)
        pts = sorted([(a, 1, sg) for a, b, R, sg, c in es] + [(b, -1, sg) for a, b, R, sg, c in es], key=lambda x: (x[0], x[1]))
        open_ = {1: 0, -1: 0}
        most = 0
        for t, d, sg in pts:
            open_[sg] += d
            most = max(most, open_[sg])
        days = (B.SPLIT - B.START if part == "train" else B.END - B.SPLIT) / 86400
        out[f"account_{part}"] = {"trades": len(es), "per_day": len(es) / days, "total_R": float(eq[-1]),
                                  "max_drawdown_R": dd, "longest_losing_streak": streak, "most_same_side_open": most}

json.dump(out, open(B.OUT / f"report{suffix}.json", "w"), indent=1, default=float)
pd = lambda r: (f"{r['trigger']:7} {r['stop']:4} {r['target']:6} {r['trend']:6} w={r['width']:5} str>={r['strength']} touches={r['touches']} | "
                f"train n={r['train_n']:5} avg={r['train_avg']:+.3f} win={r['train_win']:.0%} pf={r['train_pf']:.2f} coins+={r['train_coins_pos']} | "
                f"held n={r['hold_n']:4} avg={r['hold_avg']:+.3f} win={r['hold_win']:.0%} pf={r['hold_pf']:.2f} coins+={r['hold_coins_pos']}")
print(f"variants {out['variants']}, eligible {out['eligible']}")
print("share of variants positive: train", round(out["share_train_pos"], 3), "held", round(out["share_hold_pos"], 3),
      "eligible-on-held", out["share_elig_hold_pos"])
print("TOP 10 BY TRAINING (selection = first line):")
for r in elig[:10]:
    print(" ", pd(r))
print("OPTION AVERAGES (mean of variant avg R: train, held):")
for o, v in effects.items():
    print(" ", o, {k: (round(a, 3) if a is not None else None, round(b, 3) if b is not None else None) for k, (a, b) in v.items()})
if best:
    print("BEST BY COIN:")
    for c, v in out["best_by_coin"].items():
        print(f"  {c}: train n={v['train']['n']} avg={v['train']['avg']:+.3f} | held n={v['hold']['n']} avg={v['hold']['avg']:+.3f}")
    print("ACCOUNT:", out["account_train"], out["account_hold"])
