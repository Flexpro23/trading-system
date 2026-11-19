"""Point-in-time levels for the backtest of your level method (docs/level-method.md).

Every hour H, the levels and zones are rebuilt from candles that had CLOSED by H. Higher timeframes
are built from the 5m candles, each stamped with its close time ("known at").
Shares the range, channel and zone rules with ui/levels.py so the desk and the backtest agree.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
import levels as L                                     # noqa: E402

DATA = ROOT / "user_data" / "data" / "binance" / "futures"
FIB_TFS = ("1d", "4h", "1h")
NEAR = 0.06                                            # levels further than 6% from price are ignored (scalp)
TF_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 7 * 86400}


# ------------------------------------------------------------------ data

def load5m(coin: str, end: int = None) -> dict:
    d = pd.read_feather(DATA / f"{coin}_USDT_USDT-5m-futures.feather")
    t = d.date.values.astype("datetime64[s]").astype(np.int64)
    a = {"t": t, "o": d.open.values, "h": d.high.values, "l": d.low.values, "c": d.close.values, "v": d.volume.values}
    if end is not None:                                # keep bars that closed by `end` (truncation test)
        keep = t + 300 <= end
        a = {k: v[keep] for k, v in a.items()}
    return a


def resample(a: dict, tf: str) -> dict:
    """Candles of `tf` built from 5m, with open time t and close time tc. A bin is only complete when tc
    is reached; the last bin may be incomplete and is dropped."""
    df = pd.DataFrame({"o": a["o"], "h": a["h"], "l": a["l"], "c": a["c"], "v": a["v"]},
                      index=pd.to_datetime(a["t"], unit="s", utc=True))
    rule = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON", "1M": "MS"}[tf]
    r = df.resample(rule, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    t = r.index.values.astype("datetime64[s]").astype(np.int64)
    if tf == "1M":
        tc = (r.index + pd.offsets.MonthBegin(1)).values.astype("datetime64[s]").astype(np.int64)
    else:
        tc = t + TF_SEC[tf]
    last_bar_close = a["t"][-1] + 300
    keep = tc <= last_bar_close
    return {"t": t[keep], "tc": tc[keep], "o": r.o.values[keep], "h": r.h.values[keep], "l": r.l.values[keep],
            "c": r.c.values[keep], "v": r.v.values[keep]}


def rows(x: dict, n: int = None) -> list:
    n = len(x["t"]) if n is None else n
    return list(zip(x["t"][:n].tolist(), x["o"][:n].tolist(), x["h"][:n].tolist(), x["l"][:n].tolist(),
                    x["c"][:n].tolist(), x["v"][:n].tolist()))


# ------------------------------------------------------------------ untouched opens as events

def open_events(x: dict, tf: str, keep_visits: int = 3) -> dict:
    """Every colour flip: the level (open of the flipping candle), the time it is known (that candle's close),
    and the times the first `keep_visits` later visits are known (close of the candle starting each visit)."""
    up = x["c"] > x["o"]
    flat = x["c"] == x["o"]
    price, born, visits = [], [], []
    n = len(x["t"])
    for i in range(1, n):
        if flat[i] or flat[i - 1] or up[i] == up[i - 1]:
            continue
        lvl = x["o"][i]
        hit = (x["l"][i + 1:] <= lvl) & (x["h"][i + 1:] >= lvl)
        idx = np.nonzero(hit)[0]
        if len(idx):
            starts = idx[np.r_[True, np.diff(idx) > 1]][:keep_visits]
            vt = x["tc"][i + 1 + starts].tolist()
        else:
            vt = []
        price.append(lvl)
        born.append(x["tc"][i])
        visits.append(vt + [np.inf] * (keep_visits - len(vt)))
    return {"tf": tf, "price": np.array(price), "born": np.array(born, dtype=float),
            "visits": np.array(visits, dtype=float).reshape(-1, keep_visits)}


# ------------------------------------------------------------------ swings

def zigzag_states(x: dict, pct: float):
    """Online version of levels.zigzag: after each candle, the last confirmed swing price and the running
    extreme of the current leg (NaN until the first swing is confirmed)."""
    h, l = x["h"], x["l"]
    n = len(h)
    piv = np.full(n, np.nan)
    ext = np.full(n, np.nan)
    trend, hi_i, lo_i, e, last = 0, 0, 0, 0, np.nan
    for i in range(n):
        if trend == 0:
            hi_i = i if h[i] > h[hi_i] else hi_i
            lo_i = i if l[i] < l[lo_i] else lo_i
            if lo_i < hi_i and h[hi_i] >= l[lo_i] * (1 + pct):
                last, trend, e = l[lo_i], 1, hi_i
            elif hi_i < lo_i and l[lo_i] <= h[hi_i] * (1 - pct):
                last, trend, e = h[hi_i], -1, lo_i
        elif trend == 1:
            if h[i] > h[e]:
                e = i
            elif l[i] <= h[e] * (1 - pct):
                last, trend, e = h[e], -1, i
        else:
            if l[i] < l[e]:
                e = i
            elif h[i] >= l[e] * (1 + pct):
                last, trend, e = l[e], 1, i
        if trend:
            piv[i] = last
            ext[i] = h[e] if trend == 1 else l[e]
    return piv, ext


def rma(values: np.ndarray, n: int) -> np.ndarray:
    out = np.empty(len(values))
    acc = values[0]
    for i, v in enumerate(values):
        acc = v if i == 0 else acc + (v - acc) / n
        out[i] = acc
    return out


def true_range(x: dict) -> np.ndarray:
    pc = np.r_[x["c"][0], x["c"][:-1]]
    return np.maximum(x["h"] - x["l"], np.maximum(abs(x["h"] - pc), abs(x["l"] - pc)))


def ema(values: np.ndarray, n: int) -> np.ndarray:
    k = 2 / (n + 1)
    out = np.empty(len(values))
    acc = values[0]
    for i, v in enumerate(values):
        acc = v if i == 0 else acc + k * (v - acc)
        out[i] = acc
    return out


# ------------------------------------------------------------------ volume profile (numpy copy of levels.profile)

def profile(x4: dict, lo_t: int, hi_t: int, bins: int = 120):
    m = (x4["t"] >= lo_t) & (x4["t"] < hi_t)
    if m.sum() < 10:
        return None
    h, l, v = x4["h"][m], x4["l"][m], x4["v"][m]
    lo, hi = l.min(), h.max()
    step = (hi - lo) / bins
    a = ((l - lo) / step).astype(int)
    b = np.minimum(bins - 1, ((h - lo) / step).astype(int))
    share = v / (b - a + 1)
    diff = np.zeros(bins + 1)
    np.add.at(diff, a, share)
    np.add.at(diff, b + 1, -share)
    vol = np.cumsum(diff)[:bins]
    poc = int(np.argmax(vol))
    i = j = poc
    got, total = vol[poc], vol.sum()
    while got < L.VALUE_AREA * total and (i > 0 or j < bins - 1):
        down = vol[i - 1] if i > 0 else -1
        up = vol[j + 1] if j < bins - 1 else -1
        if up >= down:
            j += 1
            got += up
        else:
            i -= 1
            got += down
    return {"poc": lo + (poc + 0.5) * step, "vah": lo + (j + 1) * step, "val": lo + i * step}


# ------------------------------------------------------------------ the builder

class Levels:
    """Everything needed to rebuild the level map at any hour, from one coin's 5m candles."""

    def __init__(self, a: dict):
        self.a = a
        self.x = {tf: resample(a, tf) for tf in ("1h", "4h", "1d", "1w", "1M")}
        self.opens = [open_events(self.x[tf], tf) for tf in ("1M", "1w", "1d", "4h")]
        self.zz = {tf: zigzag_states(self.x[tf], L.FIB_SWING[tf]) for tf in FIB_TFS}
        x1 = self.x["1h"]
        self.ema200 = ema(x1["c"], 200)
        self.atr1h = rma(true_range(x1), 144)
        self.daily_rows = rows(self.x["1d"])
        self._day = {}

    def _closed(self, tf: str, H: int) -> int:
        """Number of `tf` candles closed by H."""
        return int(np.searchsorted(self.x[tf]["tc"], H, side="right"))

    def day_levels(self, nd: int) -> dict:
        """Ranges and channel from the first `nd` closed daily candles (cached per day)."""
        if nd in self._day:
            return self._day[nd]
        d = self.daily_rows[:nd]
        out = {"ranges": [], "channel": None}
        if nd >= 60:
            day_close = self.x["1d"]["tc"][nd - 1]
            x4 = self.x["4h"]
            m4 = x4["tc"] <= day_close
            x4c = {k: v[m4] for k, v in x4.items()}
            for lo_t, hi_t in L.ranges(d):
                pr = profile(x4c, lo_t, hi_t)
                if pr:
                    out["ranges"].append(pr)
        if nd >= 200:
            ch = L.channel(d)
            if ch and ch["width"] <= L.MAX_CHANNEL_WIDTH and not ch["broken"]:
                out["channel"] = ch
        self._day[nd] = out
        return out

    def at(self, H: int, touches: int, shift: float = 0.0) -> dict:
        """The level map at hour H: price, trend, 1h ATR and every level within NEAR of price.
        `touches`: a level is traded on its first `touches` touches, so it is alive while it has had fewer.
        `shift`: placebo, every level moved by this fraction."""
        n5 = int(np.searchsorted(self.a["t"] + 300, H, side="right"))
        px = self.a["c"][n5 - 1]
        n1 = self._closed("1h", H)
        lv = []                                         # (price, kind)
        for ev in self.opens:
            alive = (ev["born"] <= H) & ((ev["visits"] <= H).sum(axis=1) < touches)
            near = alive & (np.abs(ev["price"] - px) <= NEAR * px)
            lv += [(p, "open_" + ev["tf"]) for p in ev["price"][near]]
        dl = self.day_levels(self._closed("1d", H))
        for r in dl["ranges"]:
            lv += [(r[k], k) for k in ("vah", "poc", "val")]
        ch = dl["channel"]
        if ch:
            b0, b1 = ch["base"]
            o0, o1 = ch["other"]
            f = (H - b0["time"]) / (b1["time"] - b0["time"])
            base = b0["price"] + f * (b1["price"] - b0["price"])
            other = o0["price"] + f * (o1["price"] - o0["price"])
            lv += [(base, "channel"), (other, "channel")]
        legs = set()
        for tf in FIB_TFS:
            k = self._closed(tf, H)
            if not k:
                continue
            p0, p1 = self.zz[tf][0][k - 1], self.zz[tf][1][k - 1]
            if np.isnan(p0) or (p0, p1) in legs:
                continue
            legs.add((p0, p1))
            lv += [(p1 + (p0 - p1) * r, "fib_" + tf) for r in L.FIB_RATIOS]
        lv = [(p * (1 + shift), k) for p, k in lv if abs(p - px) <= NEAR * px]
        return {"H": H, "px": px, "ema": self.ema200[n1 - 1] if n1 else np.nan,
                "atr1h": self.atr1h[n1 - 1] if n1 else np.nan, "levels": lv}


def zones(snap: dict, width) -> list:
    """Zones from a level map: width is a fraction of price, or 'atr' (0.5 x the 1h ATR 144)."""
    w = 0.5 * snap["atr1h"] / snap["px"] if width == "atr" else width
    zs = L.zones([{"price": p, "kind": k} for p, k in snap["levels"]], w)
    return [(z["low"], z["high"], z["strength"]) for z in zs]
