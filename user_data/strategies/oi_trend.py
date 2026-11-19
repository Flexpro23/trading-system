"""Research only (docs/open-interest-test.md, H3): 20-day momentum that takes an entry only when
open interest is higher than three days before. Backtest only: it reads the downloaded archive."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from candidates import TSMomentum

OI_DIR = Path(__file__).resolve().parents[1] / "data" / "oi"
_CACHE: dict = {}


def oi_rising(pair: str, dates: pd.Series, days: int = 3) -> np.ndarray:
    """1.0 / 0.0 / nan per 4h candle: open interest (contracts) at least 5 minutes before the
    candle's close against the same reading `days` earlier."""
    coin = pair.split("/")[0]
    if coin not in _CACHE:
        z = np.load(OI_DIR / f"{coin}_5m.npz")
        s = pd.Series(z["oi"], index=z["t"].astype(np.int64))
        s = s[s > 0]
        _CACHE[coin] = s.reindex(np.arange(s.index.min(), s.index.max() + 300, 300)).ffill(limit=2)
    s = _CACHE[coin]
    now = dates.dt.as_unit("s").astype("int64").to_numpy() + 4 * 3600 - 300
    a, b = s.reindex(now).to_numpy(), s.reindex(now - days * 86400).to_numpy()
    return np.where(np.isnan(a) | np.isnan(b), np.nan, (a > b).astype(float))


class OITrend(TSMomentum):
    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        df["oi_up"] = oi_rising(metadata["pair"], df["date"])
        return df

    def populate_entry_trend(self, df, metadata):
        ok = df["oi_up"] == 1
        df.loc[(df["roc"] > 0.05) & (df["close"] > df["ema100"]) & ok, "enter_long"] = 1
        df.loc[(df["roc"] < -0.05) & (df["close"] < df["ema100"]) & ok, "enter_short"] = 1
        return df


class OIBase(TSMomentum):
    """Plain momentum on the candles where an open-interest reading exists (like-for-like base)."""
    KEEP = 1.0
    SALT = ""

    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        known = ~np.isnan(oi_rising(metadata["pair"], df["date"]))
        pair = metadata["pair"]
        coin = np.array([int(hashlib.md5(f"{self.SALT}{pair}{d.value}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                         for d in df["date"]])
        df["oi_ok"] = known & (coin < self.KEEP)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["roc"] > 0.05) & (df["close"] > df["ema100"]) & df["oi_ok"], "enter_long"] = 1
        df.loc[(df["roc"] < -0.05) & (df["close"] < df["ema100"]) & df["oi_ok"], "enter_short"] = 1
        return df


# Random drop of the same share of entry candles as the filter removes. KEEP is set from the
# measured share of momentum-signal candles with rising open interest (a property of the data).
class OIDropA(OIBase):
    KEEP, SALT = 0.5, "a"


class OIDropB(OIBase):
    KEEP, SALT = 0.5, "b"


class OIDropC(OIBase):
    KEEP, SALT = 0.5, "c"


class OIDropD(OIBase):
    KEEP, SALT = 0.5, "d"


class OIDropE(OIBase):
    KEEP, SALT = 0.5, "e"
