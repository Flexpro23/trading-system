"""Intraday round 2 (2026-11-19): 15m entries, holds of an hour to three days. Plan: docs/intraday-test-2.md."""
import hashlib

import numpy as np
import pandas as pd
import talib.abstract as ta

from candidates import RiskBase
from freqtrade.strategy import merge_informative_pair
from lab import Lab


class _Intra(Lab, RiskBase):
    """15m candles with the 4h momentum rule and the 1h ATR merged in (closed candles only)."""
    timeframe = "15m"
    STOP_ATR = 2.0
    TRAIL = False
    HOLD_HOURS = 72.0
    startup_candle_count = 600
    TREND = True

    def informative_pairs(self):
        wl = self.dp.current_whitelist()
        return [(p, "1h") for p in wl] + [(p, "4h") for p in wl]

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        pair = metadata["pair"]
        h1 = self.dp.get_pair_dataframe(pair, "1h")
        h1["atr1h"] = ta.ATR(h1, timeperiod=14)
        h1["rsi1h"] = ta.RSI(h1, timeperiod=14)
        df = merge_informative_pair(df, h1[["date", "atr1h", "rsi1h"]], self.timeframe, "1h", ffill=True)
        h4 = self.dp.get_pair_dataframe(pair, "4h")
        h4["roc"] = h4["close"] / h4["close"].shift(120) - 1
        h4["ema100"] = ta.EMA(h4, timeperiod=100)
        h4["up"] = ((h4["roc"] > 0.05) & (h4["close"] > h4["ema100"])).astype(int)
        h4["dn"] = ((h4["roc"] < -0.05) & (h4["close"] < h4["ema100"])).astype(int)
        h4["xup"] = ((h4["roc"] < 0) | (h4["close"] < h4["ema100"])).astype(int)
        h4["xdn"] = ((h4["roc"] > 0) | (h4["close"] > h4["ema100"])).astype(int)
        df = merge_informative_pair(df, h4[["date", "up", "dn", "xup", "xdn"]], self.timeframe, "4h", ffill=True)
        df["atr"] = df["atr1h_1h"]                 # stops and sizing use the 1h ATR
        df["ema20"] = ta.EMA(df, timeperiod=20)
        return df

    def populate_exit_trend(self, df, metadata):
        if self.TREND:
            df.loc[df["xup_4h"] == 1, "exit_long"] = 1
            df.loc[df["xdn_4h"] == 1, "exit_short"] = 1
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.HOLD_HOURS * 3600:
            return "time_exit"
        return super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)


class IntraPullback(_Intra):
    def populate_entry_trend(self, df, metadata):
        dipped = df["rsi1h_1h"].rolling(16).min() < 40
        popped = df["rsi1h_1h"].rolling(16).max() > 60
        up = (df["close"] > df["ema20"]) & (df["close"].shift(1) <= df["ema20"].shift(1))
        dn = (df["close"] < df["ema20"]) & (df["close"].shift(1) >= df["ema20"].shift(1))
        df.loc[(df["up_4h"] == 1) & dipped & up, "enter_long"] = 1
        df.loc[(df["dn_4h"] == 1) & popped & dn, "enter_short"] = 1
        return df


class IntraPullback2R(IntraPullback):
    REWARD_R = 2.0


class IntraBreakout(_Intra):
    def populate_entry_trend(self, df, metadata):
        hi, lo = df["high"].rolling(96).max().shift(1), df["low"].rolling(96).min().shift(1)
        df.loc[(df["up_4h"] == 1) & (df["close"] > hi) & (df["close"].shift(1) <= hi.shift(1)), "enter_long"] = 1
        df.loc[(df["dn_4h"] == 1) & (df["close"] < lo) & (df["close"].shift(1) >= lo.shift(1)), "enter_short"] = 1
        return df


class IntraBreakout2R(IntraBreakout):
    REWARD_R = 2.0


def _coin(pair, dates, salt):
    return np.array([int(hashlib.md5(f"{salt}{pair}{d.value}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF for d in dates])


class IntraFlipA(_Intra):
    """Control for A and B: random entries inside the same trend, in its direction, same stop and exits."""
    SALT, P = "a", 0.01

    def populate_entry_trend(self, df, metadata):
        coin = _coin(metadata["pair"], df["date"], self.SALT)
        df.loc[(df["up_4h"] == 1) & (coin < self.P), "enter_long"] = 1
        df.loc[(df["dn_4h"] == 1) & (coin < self.P), "enter_short"] = 1
        return df


class IntraFlipB(IntraFlipA):
    SALT = "b"


class IntraFlipC(IntraFlipA):
    SALT = "c"


class IntraFlipD(IntraFlipA):
    SALT = "d"


class IntraFlipE(IntraFlipA):
    SALT = "e"


class IntraBtcLead(_Intra):
    STOP_ATR = 1.0
    REWARD_R = 1.5
    HOLD_HOURS = 4.0
    TREND = False

    def informative_pairs(self):
        return super().informative_pairs() + [("BTC/USDT:USDT", "15m")]

    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        b = self.dp.get_pair_dataframe("BTC/USDT:USDT", "15m")
        r = b["close"] / b["open"] - 1
        bz = (r / r.rolling(288).std().shift(1)).set_axis(b["date"])
        df["btc_z"] = bz.reindex(df["date"]).values
        mine = df["close"] / df["open"] - 1
        df["my_z"] = mine / mine.rolling(288).std().shift(1)
        return df

    def populate_entry_trend(self, df, metadata):
        if metadata["pair"].startswith("BTC/"):
            return df
        df.loc[(df["btc_z"] >= 3) & (df["my_z"] < 1), "enter_long"] = 1
        df.loc[(df["btc_z"] <= -3) & (df["my_z"] > -1), "enter_short"] = 1
        return df


# ------------------------------------------------------------------ 1h relative strength
class _RS1h(Lab, RiskBase):
    timeframe = "1h"
    STOP_ATR = 3.0
    TRAIL = False
    startup_candle_count = 300
    LOOK = 72

    def informative_pairs(self):
        return [(p, self.timeframe) for p in self.dp.current_whitelist()]

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        rocs = {}
        for p in self.dp.current_whitelist():
            d = self.dp.get_pair_dataframe(p, self.timeframe)
            rocs[p] = (d["close"] / d["close"].shift(self.LOOK) - 1).set_axis(d["date"])
        table = pd.DataFrame(rocs)
        n = table.notna().sum(axis=1).reindex(df["date"]).values
        r = table.rank(axis=1, ascending=False)[metadata["pair"]].reindex(df["date"]).values
        full = n >= 6
        df["rs_top"], df["rs_bot"] = full & (r <= 2), full & (r >= n - 1)
        df["rs_not_top"], df["rs_not_bot"] = ~full | (r > 3), ~full | (r < n - 2)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["rs_top"], "enter_long"] = 1
        df.loc[df["rs_bot"], "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["rs_not_top"], "exit_long"] = 1
        df.loc[df["rs_not_bot"], "exit_short"] = 1
        return df


class IntraRS3d(_RS1h):
    LOOK = 72


class IntraRS1d(_RS1h):
    LOOK = 24


class IntraRSFlipA(Lab, RiskBase):
    """Control for C: random long and short entries on 1h candles, same stop, closed after 2 days."""
    timeframe = "1h"
    STOP_ATR = 3.0
    TRAIL = False
    SALT, P = "a", 0.02

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["coin"] = _coin(metadata["pair"], df["date"], self.SALT)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["coin"] < self.P, "enter_long"] = 1
        df.loc[df["coin"] > 1 - self.P, "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return "time_exit" if (current_time - trade.open_date_utc).total_seconds() >= 48 * 3600 else None


class IntraRSFlipB(IntraRSFlipA):
    SALT = "b"


class IntraRSFlipC(IntraRSFlipA):
    SALT = "c"


class IntraRSFlipD(IntraRSFlipA):
    SALT = "d"


class IntraRSFlipE(IntraRSFlipA):
    SALT = "e"
