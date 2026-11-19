"""Your level method as code: untouched opens, range volume profiles, Fibonacci, channel, and zones
where several of them meet. See docs/level-method.md. Pure functions over candle lists, so the
desk and the backtest use the same code.

A candle is (time_s, open, high, low, close, volume).
"""
import time

import requests

MAX_TOUCHES = 3              # a level is removed when price comes back to it this many times
FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.66, 0.786, 0.886)
FIB_SWING = {"1d": 0.15, "4h": 0.08, "1h": 0.03}   # zigzag size that counts as a swing on each timeframe
VALUE_AREA = 0.70
MAX_CHANNEL_WIDTH = 0.50     # top more than 50% above the bottom: no clean channel, so its lines are not levels
OPEN_TFS = ("1M", "1w", "1d", "4h")
TF_NAME = {"1M": "Monthly", "1w": "Weekly", "1d": "Daily", "4h": "4h", "1h": "1h"}


# ------------------------------------------------------------------ data

def klines(sym: str, tf: str, limit: int = 1500, pages: int = 1) -> list:
    """Closed and current candles from Binance USDT futures, oldest first."""
    out, end = [], None
    for _ in range(pages):
        prm = {"symbol": sym, "interval": tf, "limit": limit}
        if end:
            prm["endTime"] = end
        rows = requests.get("https://fapi.binance.com/fapi/v1/klines", params=prm, timeout=10).json()
        if not rows:
            break
        out = [(r[0] // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows] + out
        end = rows[0][0] - 1
        if len(rows) < limit:
            break
    return out


# ------------------------------------------------------------------ untouched opens

def flip_opens(c: list, tf: str, max_touches: int = MAX_TOUCHES) -> list:
    """When a candle's colour flips against the one before, its open becomes a level.
    Every later visit to the price is a touch (a run of candles sitting on it is one touch).
    The level is gone once it has `max_touches` touches. The last, still-open candle cannot make a level."""
    out = []
    for i in range(1, len(c) - 1):
        a, b = c[i - 1], c[i]
        up_a, up_b = a[4] > a[1], b[4] > b[1]
        if a[4] == a[1] or b[4] == b[1] or up_a == up_b:
            continue
        lvl, touches, on = b[1], 0, False
        for x in c[i + 1:]:
            hit = x[3] <= lvl <= x[2]
            if hit and not on:
                touches += 1
            on = hit
        if touches < max_touches:
            out.append({"kind": "open", "tf": tf, "price": lvl, "time": b[0], "touches": touches,
                        "label": f"{TF_NAME[tf]} open", "turn": "down" if up_a else "up"})
    return out


# ------------------------------------------------------------------ ranges and volume profile

def atr(c: list, n: int = 14) -> list:
    out, prev = [], None
    for x in c:
        tr = x[2] - x[3] if prev is None else max(x[2] - x[3], abs(x[2] - prev), abs(x[3] - prev))
        out.append(tr if not out else out[-1] + (tr - out[-1]) / n)
        prev = x[4]
    return out


def ranges(daily: list, impulse_atr: float = 2.0, min_days: int = 20) -> list:
    """Sideways stretches: the time between impulse moves. An impulse is a day, or two days together,
    that moves more than `impulse_atr` times the daily ATR. Stretches shorter than `min_days` are skipped."""
    a = atr(daily)
    imp = set()
    for i in range(15, len(daily)):
        if abs(daily[i][4] - daily[i][1]) >= impulse_atr * a[i - 1]:
            imp.add(i)
        if abs(daily[i][4] - daily[i - 1][1]) >= 1.5 * impulse_atr * a[i - 2]:
            imp.update((i - 1, i))
    out, start = [], 15
    for i in sorted(imp) + [len(daily)]:
        if i - start >= min_days:
            out.append((daily[start][0], daily[i - 1][0] + 86400))
        start = max(start, i + 1)
    return out


def profile(c: list, lo_t: int, hi_t: int, bins: int = 120) -> dict:
    """Fixed-range volume profile of the candles inside [lo_t, hi_t): point of control and the
    value area around it holding 70% of the volume. Each candle's volume is spread evenly over its range."""
    part = [x for x in c if lo_t <= x[0] < hi_t]
    if len(part) < 10:
        return None
    lo, hi = min(x[3] for x in part), max(x[2] for x in part)
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for x in part:
        a, b = int((x[3] - lo) / step), min(bins - 1, int((x[2] - lo) / step))
        share = x[5] / (b - a + 1)
        for k in range(a, b + 1):
            vol[k] += share
    poc = max(range(bins), key=vol.__getitem__)
    i = j = poc
    got, total = vol[poc], sum(vol)
    while got < VALUE_AREA * total and (i > 0 or j < bins - 1):
        down = vol[i - 1] if i > 0 else -1
        up = vol[j + 1] if j < bins - 1 else -1
        if up >= down:
            j += 1
            got += up
        else:
            i -= 1
            got += down
    mid = lambda k: lo + (k + 0.5) * step
    return {"start": part[0][0], "end": part[-1][0], "poc": mid(poc), "vah": lo + (j + 1) * step, "val": lo + i * step,
            "high": hi, "low": lo}


# ------------------------------------------------------------------ swings, Fibonacci, channel

def zigzag(c: list, pct: float) -> list:
    """Swing points: a new swing starts once price turns `pct` from the last extreme.
    Returns [(index, price, 'high'|'low')]; the last one is the running extreme of the current leg."""
    piv, trend, hi_i, lo_i, ext = [], 0, 0, 0, 0
    for i, x in enumerate(c):
        if trend == 0:
            hi_i = i if x[2] > c[hi_i][2] else hi_i
            lo_i = i if x[3] < c[lo_i][3] else lo_i
            if lo_i < hi_i and c[hi_i][2] >= c[lo_i][3] * (1 + pct):
                piv.append((lo_i, c[lo_i][3], "low")); trend, ext = 1, hi_i
            elif hi_i < lo_i and c[lo_i][3] <= c[hi_i][2] * (1 - pct):
                piv.append((hi_i, c[hi_i][2], "high")); trend, ext = -1, lo_i
        elif trend == 1:
            if x[2] > c[ext][2]:
                ext = i
            elif x[3] <= c[ext][2] * (1 - pct):
                piv.append((ext, c[ext][2], "high")); trend, ext = -1, i
        else:
            if x[3] < c[ext][3]:
                ext = i
            elif x[2] >= c[ext][3] * (1 + pct):
                piv.append((ext, c[ext][3], "low")); trend, ext = 1, i
    if trend == 1:
        piv.append((ext, c[ext][2], "high"))
    elif trend == -1:
        piv.append((ext, c[ext][3], "low"))
    return piv


def fib(c: list, tf: str) -> dict:
    """Fibonacci of the latest swing: from the last swing point to the extreme since."""
    z = zigzag(c, FIB_SWING[tf])
    if len(z) < 2:
        return None
    (i0, p0, _), (i1, p1, k1) = z[-2], z[-1]
    lv = [{"kind": "fib", "tf": tf, "price": p1 + (p0 - p1) * r, "ratio": r, "label": f"{TF_NAME[tf]} Fib {r:g}"}
          for r in FIB_RATIOS]
    return {"tf": tf, "from": {"time": c[i0][0], "price": p0}, "to": {"time": c[i1][0], "price": p1},
            "leg": "down" if k1 == "low" else "up", "levels": lv}


def pivots(c: list, left: int = 3, right: int = 2) -> tuple:
    """Swing lows and highs: no candle `left` before or `right` after goes beyond them."""
    lows = [i for i in range(left, len(c) - right) if c[i][3] == min(x[3] for x in c[i - left:i + right + 1])]
    highs = [i for i in range(left, len(c) - right) if c[i][2] == max(x[2] for x in c[i - left:i + right + 1])]
    return lows, highs


def channel(daily: list, days: int = 365, min_gap: int = 20) -> dict:
    """A channel on the daily, drawn the way you draw it.
    Rising: from the lowest low of the last year, the line to a later swing low that no low has broken since;
    the top line runs parallel through the highest high since. Falling: the same from the highest high.
    The one starting from the more recent extreme is the channel price is in now."""
    c = daily[-days:]
    lows, highs = pivots(c)
    t = lambda i: c[0][0] + i * 86400
    found = []
    for rising in (True, False):
        i0 = (min if rising else max)(range(len(c)), key=lambda i: c[i][3] if rising else c[i][2])
        p0 = c[i0][3] if rising else c[i0][2]
        cand = [i for i in (lows if rising else highs) if i >= i0 + min_gap]
        if not cand:
            continue
        val = lambda i: c[i][3] if rising else c[i][2]
        i1 = (min if rising else max)(cand, key=lambda i: (val(i) - p0) / (i - i0))
        slope = (val(i1) - p0) / (i1 - i0)
        line = lambda i: p0 + slope * (i - i0)
        span = range(i0, len(c))
        off = max(c[i][2] - line(i) for i in span) if rising else min(c[i][3] - line(i) for i in span)
        end = len(c) + 30
        now = len(c) - 1
        found.append({"start": i0, "anchor": "lows" if rising else "highs", "rising": slope > 0,
                      "points": [{"time": t(i0), "price": p0}, {"time": t(i1), "price": val(i1)}],
                      "base": [{"time": t(i0), "price": p0}, {"time": t(end), "price": line(end)}],
                      "other": [{"time": t(i0), "price": p0 + off}, {"time": t(end), "price": line(end) + off}],
                      "mid": [{"time": t(i0), "price": p0 + off / 2}, {"time": t(end), "price": line(end) + off / 2}],
                      "bottom_now": min(line(now), line(now) + off), "top_now": max(line(now), line(now) + off),
                      "width": abs(off) / min(line(now), line(now) + off),
                      "broken": not min(line(now), line(now) + off) <= c[now][4] <= max(line(now), line(now) + off)})
    if not found:
        return None
    ch = max(found, key=lambda f: f["start"])
    ch.pop("start")
    return ch


# ------------------------------------------------------------------ zones

def zones(levels: list, width: float) -> list:
    """Merge levels lying within `width` (a fraction of price) of the zone's first level."""
    out = []
    for lv in sorted(levels, key=lambda l: l["price"]):
        if out and lv["price"] - out[-1]["low"] <= width * out[-1]["low"]:
            z = out[-1]
            z["levels"].append(lv)
            z["high"] = lv["price"]
        else:
            out.append({"low": lv["price"], "high": lv["price"], "levels": [lv]})
    for z in out:
        z["price"] = sum(l["price"] for l in z["levels"]) / len(z["levels"])
        z["strength"] = len(z["levels"])
    return out


# ------------------------------------------------------------------ everything for one coin

def analyse(sym: str, near: float = 0.25, width: float = 0.005, show_touches: int = 1, near_4h: float = 0.04) -> dict:
    """All levels for one coin within `near` (fraction) of the current price, plus zones.
    Opens are shown while they have at most `show_touches` touches (they stay in the method until
    MAX_TOUCHES); 4h opens only within `near_4h`, since there are many of them.
    The same open on several timeframes is kept once, under the highest timeframe."""
    data = {"1M": klines(sym, "1M", 200), "1w": klines(sym, "1w", 500), "1d": klines(sym, "1d", 1500),
            "4h": klines(sym, "4h", 1500, pages=2), "1h": klines(sym, "1h", 1500)}
    px = data["1h"][-1][4]
    keep = lambda p, f=near: abs(p - px) <= f * px
    levels, seen = [], {}
    for tf in OPEN_TFS:
        for l in flip_opens(data[tf], tf):
            if l["touches"] > show_touches or not keep(l["price"], near_4h if tf == "4h" else near):
                continue
            key = round(l["price"], 8)
            if key in seen:
                seen[key]["also"].append(TF_NAME[tf])
                continue
            l["also"] = []
            seen[key] = l
            levels.append(l)
    rngs = []
    for lo_t, hi_t in ranges(data["1d"]):
        pr = profile(data["4h"], lo_t, hi_t)
        if pr and any(keep(pr[k]) for k in ("poc", "vah", "val")):
            rngs.append(pr)
            for k, name in (("vah", "VAH"), ("poc", "POC"), ("val", "VAL")):
                if keep(pr[k]):
                    levels.append({"kind": k, "tf": "range", "price": pr[k], "time": pr["start"], "end": pr["end"],
                                   "label": f"Range {name}"})
    fibs, legs = [], set()
    for tf in ("1d", "4h", "1h"):
        f = fib(data[tf], tf)
        if not f:
            continue
        leg = (round(f["from"]["price"], 8), round(f["to"]["price"], 8))
        if leg in legs:                       # the same swing seen on a higher timeframe
            continue
        legs.add(leg)
        fibs.append(f)
        levels += [l for l in f["levels"] if keep(l["price"])]
    ch = channel(data["1d"])
    if ch and ch["width"] <= MAX_CHANNEL_WIDTH and not ch["broken"]:   # price closed outside: the channel is broken          # a wider "channel" is not one you would draw
        for k, name in (("bottom_now", "Channel bottom"), ("top_now", "Channel top")):
            if keep(ch[k]):
                levels.append({"kind": "channel", "tf": "1d", "price": ch[k], "label": name})
    return {"price": px, "updated": time.time(), "levels": sorted(levels, key=lambda l: -l["price"]),
            "ranges": rngs, "fibs": fibs, "channel": ch, "zones": zones(levels, width), "width": width}
