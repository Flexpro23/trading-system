"""Entry rule of the scalp bot: a touch of one of your zones, then a volume-delta signal on a closed candle.

The same three signals as the order-flow backtest (docs/order-flow-test.md, backtest/flow_bt.py):
  flip   : a candle closes back out of the zone with heavy net buying (selling for a short)
  absorb : a candle trades into the zone on heavy net selling and still closes in its upper half
  ddiv   : price makes a lower pivot low at the zone while cumulative delta makes a higher one
Shorts are longs on mirrored prices with delta negated, so each rule is written once.
Not identical to the backtest: zones here are the ones drawn on the desk's chart at the time of the signal.
"""
import numpy as np

SEC = {"1m": 60, "5m": 300, "15m": 900}
WAIT = {"1m": 60, "5m": 36, "15m": 24}                  # candles a signal may take after the touch
HOLD = {"1m": 4 * 3600, "5m": 24 * 3600, "15m": 60 * 3600}   # a trade is closed after this many seconds
MIN_STOP = {"1m": 0.0015, "5m": 0.002, "15m": 0.003}    # smallest stop distance, as a fraction of price
ZT, ZWIN, ATR_N = 1.5, 288, 144
TRIGGERS = ("flip", "absorb", "ddiv")


def _rma(x, n):
    out = np.empty(len(x))
    acc = x[0]
    for i, v in enumerate(x):
        acc = v if i == 0 else acc + (v - acc) / n
        out[i] = acc
    return out


def _pivot_low(L, k, w=2):
    return k - w >= 0 and k + w < len(L) and L[k] == L[k - w:k + w + 1].min()


def signal(rows, zones, trigger, tf, min_strength=2):
    """rows: closed candles, oldest first, each (time, open, high, low, close, volume, taker_buy_volume).
    zones: dicts with low, high and strength. Returns None, or the entry found on the last closed candle:
    {"side", "stop", "zone": (low, high), "strength", "why"}. The stop is one ATR beyond the zone."""
    if len(rows) < ZWIN + 5:
        return None
    a = np.array(rows, dtype=float)
    h, l, c, delta = a[:, 2], a[:, 3], a[:, 4], 2 * a[:, 6] - a[:, 5]
    pc = np.r_[c[0], c[:-1]]
    atr = _rma(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), ATR_N)
    j = len(c) - 1
    sd = delta[j - ZWIN:j].std()
    z = delta[j] / sd if sd > 0 else 0.0
    cvd = np.cumsum(delta)
    best = None
    for zn in zones:
        if zn.get("strength", 1) < min_strength:
            continue
        for sg in (1, -1):
            H, L, C, Z, CV = (h, l, c, z, cvd) if sg > 0 else (-l, -h, -c, -z, -cvd)
            zl, zh = (zn["low"], zn["high"]) if sg > 0 else (-zn["high"], -zn["low"])
            lo = max(1, j - WAIT[tf] + 1)
            hit = np.flatnonzero(L[lo:j + 1] <= zh)
            if not len(hit):
                continue
            i = lo + hit[0]                               # first touch inside the waiting window
            if C[i - 1] <= zh or (C[i:j + 1] < zl - atr[i - 1]).any():
                continue                                  # price did not come from the far side, or broke through
            if trigger == "flip":
                ok = C[j] > zh and Z >= ZT
            elif trigger == "absorb":
                ok = L[j] <= zh and Z <= -ZT and C[j] >= (H[j] + L[j]) / 2 and C[j] > zl
            else:
                k, tol = j - 2, max(0.0025 * abs(zh), zh - zl)
                ok = False
                if k >= i and _pivot_low(L, k) and L[k] <= zh + tol:
                    p = next((p for p in range(k - 4, max(2, k - 40) - 1, -1) if _pivot_low(L, p) and L[p] > L[k]), None)
                    ok = p is not None and CV[k] > CV[p]
            if ok:
                dist = abs(C[j] - zh)
                if best is None or dist < best[0]:
                    stop = sg * (zl - atr[j - 1])
                    best = (dist, {"side": "long" if sg > 0 else "short", "stop": float(stop),
                                   "zone": (zn["low"], zn["high"]), "strength": zn.get("strength", 1),
                                   "why": f"{trigger} at zone {zn['low']:g}-{zn['high']:g}, delta {z:+.1f} sd"})
    return best[1] if best else None
