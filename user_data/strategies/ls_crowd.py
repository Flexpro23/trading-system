"""Research (docs/long-short-ratio-test.md, stage 2): fade the all-accounts long/short ratio.
Long when the ratio is in its lowest 5% of the previous 90 days, short when in its highest 5%.
1h candles, stop 6 x ATR(1h), closed after HOLD_H hours. Backtest only: reads the downloaded archive."""
from pathlib import Path

import numpy as np
import pandas as pd

from oi_flush import OIFlipA, _Hold72

OI_DIR = Path(__file__).resolve().parents[1] / "data" / "oi"
_CACHE: dict = {}


def ratio(pair: str, dates: pd.Series, col: str = "ls") -> np.ndarray:
    coin = pair.split("/")[0]
    if (coin, col) not in _CACHE:
        z = np.load(OI_DIR / f"{coin}_5m.npz")
        s = pd.Series(z[col], index=z["t"].astype(np.int64))
        s = s[s > 0]
        _CACHE[(coin, col)] = s.reindex(np.arange(s.index.min(), s.index.max() + 300, 300)).ffill(limit=2)
    now = dates.dt.as_unit("s").astype("int64").to_numpy() + 3600 - 300
    return _CACHE[(coin, col)].reindex(now).to_numpy()


class LSCrowd72(_Hold72):
    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["ls"] = ratio(metadata["pair"], df["date"])
        past = df["ls"].shift(1).rolling(2160, min_periods=1500)
        df["ls_lo"], df["ls_hi"] = past.quantile(0.05), past.quantile(0.95)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["ls"] <= df["ls_lo"], "enter_long"] = 1
        df.loc[df["ls"] >= df["ls_hi"], "enter_short"] = 1
        return df


class LSCrowd24(LSCrowd72):
    HOLD_H = 24


class LSPrice72(_Hold72):
    """The price version: long when the 3-day return is in its highest 5%, short in its lowest 5%."""

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["r3"] = df["close"] / df["close"].shift(72) - 1
        past = df["r3"].shift(1).rolling(2160, min_periods=1500)
        df["r3_lo"], df["r3_hi"] = past.quantile(0.05), past.quantile(0.95)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["r3"] >= df["r3_hi"], "enter_long"] = 1
        df.loc[df["r3"] <= df["r3_lo"], "enter_short"] = 1
        return df


class LSPrice24(LSPrice72):
    HOLD_H = 24


class LSFlip24A(OIFlipA):
    HOLD_H, P = 24, 0.004


class LSFlip24B(LSFlip24A):
    SALT = "b"


class LSFlip24C(LSFlip24A):
    SALT = "c"


class LSFlip24D(LSFlip24A):
    SALT = "d"


class LSFlip24E(LSFlip24A):
    SALT = "e"
