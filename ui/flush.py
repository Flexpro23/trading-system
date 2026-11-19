"""Flush signal: a large one-way candle on heavy volume with one-sided taker flow (a proxy for a
liquidation cascade). Used by the backtest (backtest/flush_bt.py); plan in docs/flush-fade-test.md."""
import numpy as np
import pandas as pd

ATR_N, VWIN, VMULT = 144, 288, 3.0


def baselines(h, l, c, v):
    """ATR(144) and the 288-candle median volume, both from candles before each candle."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / ATR_N, adjust=False).mean().shift(1).to_numpy(copy=True)
    atr[:ATR_N] = np.nan
    vmed = pd.Series(v).rolling(VWIN).median().shift(1).values
    return atr, vmed


def flush_side(o, h, l, c, v, tb, k, flow=True):
    """+1 where a candle is a flush down (fade it long), -1 for a squeeze up, else 0.
    flow=False drops the volume and taker conditions (the price-move-only reference)."""
    atr, vmed = baselines(h, l, c, v)
    with np.errstate(invalid="ignore"):
        down, up = (o - c) >= k * atr, (c - o) >= k * atr
        if flow:
            heavy = v >= VMULT * vmed
            down &= heavy & (v - tb > tb)
            up &= heavy & (tb > v - tb)
    return np.where(down, 1, np.where(up, -1, 0)), atr
