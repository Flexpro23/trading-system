"""Real levels vs the shifted placebo, variant by variant, and the best variants before and after fees.
Run with ENTRY=5m|15m|1h."""
import itertools
import pickle

import numpy as np

import level_bt as B

tag = "" if B.ENTRY == "5m" else "_" + B.ENTRY
load = lambda sfx: {c: pickle.load(open(B.OUT / f"{c}{tag}{sfx}.pkl", "rb")) for c in B.COINS}
real, plc, nof = load(""), load("_placebo"), load("_nofees")


def avg(runs, key, st, tr, part):
    R = []
    for r in runs.values():
        t = B.select(r["results"][key], st, tr)
        ti = r["t"][t["j"]]
        R.append(t["R"][(ti < B.SPLIT) if part == "train" else (ti >= B.SPLIT)])
    R = np.concatenate(R)
    return (R.mean() if len(R) else np.nan), len(R)


rows = []
for key in real["BTC"]["results"]:
    for st, tr in itertools.product(B.STRENGTHS, B.TRENDS):
        r = {p: avg(real, key, st, tr, p) for p in ("train", "hold")}
        n = {p: avg(nof, key, st, tr, p) for p in ("train", "hold")}
        pl = {p: np.nanmean([avg(plc, (s,) + key[1:], st, tr, p)[0] for s in (0.008, -0.008)]) for p in ("train", "hold")}
        rows.append((key, st, tr, r, n, pl))

d = np.array([x[3]["train"][0] - x[5]["train"] for x in rows if x[3]["train"][1] >= 30])
dh = np.array([x[3]["hold"][0] - x[5]["hold"] for x in rows if x[3]["hold"][1] >= 30])
print(f"ENTRY {B.ENTRY}: real minus placebo, avg R per trade. train mean {np.nanmean(d):+.4f}, real better in {np.mean(d > 0):.0%}; "
      f"held mean {np.nanmean(dh):+.4f}, real better in {np.mean(dh > 0):.0%}")
rows.sort(key=lambda x: -x[4]["train"][0] if x[4]["train"][1] >= 150 else 9)
print("Best 5 before fees (training), with after-fee and placebo results:")
for key, st, tr, r, n, pl in rows[:5]:
    print(f"  {key[3]:7} {key[4]:4} {key[5]:6} {tr:6} w={key[2]} str>={st} t={key[1]} | before fees train {n['train'][0]:+.3f} held {n['hold'][0]:+.3f}"
          f" | after fees train {r['train'][0]:+.3f} held {r['hold'][0]:+.3f} (n {r['train'][1]}/{r['hold'][1]})"
          f" | placebo train {pl['train']:+.3f} held {pl['hold']:+.3f}")
