"""Research (docs/open-interest-test.md, H1 as a strategy): fade a sharp 4-hour price move that came
with a sharp fall in open interest. 1h candles, stop 6 x ATR(1h), closed after 72 hours.
Backtest only: it reads the downloaded archive."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from candidates import RiskBase

OI_DIR = Path(__file__).resolve().parents[1] / "data" / "oi"
_CACHE: dict = {}


def oi_change(pair: str, dates: pd.Series, hours: int = 4) -> np.ndarray:
    coin = pair.split("/")[0]
    if coin not in _CACHE:
        z = np.load(OI_DIR / f"{coin}_5m.npz")
        s = pd.Series(z["oi"], index=z["t"].astype(np.int64))
        s = s[s > 0]
        _CACHE[coin] = s.reindex(np.arange(s.index.min(), s.index.max() + 300, 300)).ffill(limit=2)
    s = _CACHE[coin]
    now = dates.dt.as_unit("s").astype("int64").to_numpy() + 3600 - 300
    return s.reindex(now).to_numpy() / s.reindex(now - hours * 3600).to_numpy() - 1


class _Hold72(RiskBase):
    timeframe = "1h"
    STOP_ATR = 6.0
    TRAIL = False
    startup_candle_count = 2200
    HOLD_H = 72

    def populate_exit_trend(self, df, metadata):
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.HOLD_H * 3600:
            return "time_exit"
        return None


class OIFlush(_Hold72):
    LONG, SHORT = True, True

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["chg"] = oi_change(metadata["pair"], df["date"])
        df["q5"] = df["chg"].shift(1).rolling(2160, min_periods=1500).quantile(0.05)
        r = df["close"] / df["close"].shift(4) - 1
        df["z"] = r / r.shift(1).rolling(720).std()
        return df

    def populate_entry_trend(self, df, metadata):
        low = df["chg"] <= df["q5"]
        if self.LONG:
            df.loc[(df["z"] <= -2) & low, "enter_long"] = 1
        if self.SHORT:
            df.loc[(df["z"] >= 2) & low, "enter_short"] = 1
        return df


class OIFlushLong(OIFlush):
    """Chosen after the event study showed the short side losing: not a pre-planned variant."""
    SHORT = False


class OIFlipA(_Hold72):
    """Control: random 1h entries, same stop and hold."""
    P, SALT, LONG_ONLY = 0.002, "a", False

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        pair = metadata["pair"]
        df["coin"] = [int(hashlib.md5(f"{self.SALT}{pair}{d.value}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                      for d in df["date"]]
        return df

    def populate_entry_trend(self, df, metadata):
        if self.LONG_ONLY:
            df.loc[df["coin"] < 2 * self.P, "enter_long"] = 1
        else:
            df.loc[df["coin"] < self.P, "enter_long"] = 1
            df.loc[df["coin"] > 1 - self.P, "enter_short"] = 1
        return df


class OIFlipB(OIFlipA):
    SALT = "b"


class OIFlipC(OIFlipA):
    SALT = "c"


class OIFlipD(OIFlipA):
    SALT = "d"


class OIFlipE(OIFlipA):
    SALT = "e"


class OILongFlipA(OIFlipA):
    LONG_ONLY = True


class OILongFlipB(OIFlipA):
    SALT, LONG_ONLY = "b", True


class OILongFlipC(OIFlipA):
    SALT, LONG_ONLY = "c", True


class OILongFlipD(OIFlipA):
    SALT, LONG_ONLY = "d", True


class OILongFlipE(OIFlipA):
    SALT, LONG_ONLY = "e", True
