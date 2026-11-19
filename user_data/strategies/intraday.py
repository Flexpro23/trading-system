"""
Intraday research candidates (2026-11-16). Fixed set, no parameter sweeps.
They reuse RiskBase from candidates.py and are not used by the running paper bot.

Horizon ladder: the momentum rule that passed on 4h (TSMomentum, 20-day lookback),
shortened step by step to see how short the hold can get before costs remove the edge.
  Mom5d  1h candles, 5-day lookback
  Mom2d  1h candles, 2-day lookback
  Mom1d  1h candles, 1-day lookback
  Mom6h  15m candles, 6-hour lookback
Thresholds scale with the square root of the lookback (5% at 20 days).

VwapScalp: the VWAP + EMA(8) + RSI(3) scalping rules from Lewis Jackson's bot_rules.json,
on 5m candles with his 0.3% stop.
"""
import numpy as np
import talib.abstract as ta

from candidates import RiskBase
from freqtrade.strategy import stoploss_from_absolute


class _Mom(RiskBase):
    STOP_ATR = 5.0
    TRAIL = False
    LOOKBACK = 120
    THRESH = 0.025

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["roc"] = df["close"] / df["close"].shift(self.LOOKBACK) - 1
        df["ema_l"] = ta.EMA(df, timeperiod=self.LOOKBACK)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["roc"] > self.THRESH) & (df["close"] > df["ema_l"]), "enter_long"] = 1
        df.loc[(df["roc"] < -self.THRESH) & (df["close"] < df["ema_l"]), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[(df["roc"] < 0) | (df["close"] < df["ema_l"]), "exit_long"] = 1
        df.loc[(df["roc"] > 0) | (df["close"] > df["ema_l"]), "exit_short"] = 1
        return df


class Mom5d(_Mom):
    timeframe = "1h"
    LOOKBACK = 120
    THRESH = 0.025


class Mom2d(_Mom):
    timeframe = "1h"
    LOOKBACK = 48
    THRESH = 0.016


class Mom1d(_Mom):
    timeframe = "1h"
    LOOKBACK = 24
    THRESH = 0.011


class Mom6h(_Mom):
    timeframe = "15m"
    LOOKBACK = 24
    THRESH = 0.0056


class _PctStop(RiskBase):
    """Fixed percentage stop instead of an ATR stop; size still risks RISK_PER_TRADE."""
    STOP_PCT = 0.003
    TRAIL = False

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake,
                            max_stake, leverage, entry_tag, side, **kwargs) -> float:
        if self.wallets is None:
            return proposed_stake
        notional = self.wallets.get_total_stake_amount() * self.RISK_PER_TRADE / self.STOP_PCT
        return min(notional / leverage, max_stake)

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit,
                        after_fill, **kwargs):
        d = trade.open_rate * self.STOP_PCT
        stop = trade.open_rate + d if trade.is_short else trade.open_rate - d
        return stoploss_from_absolute(stop, current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return None


class VwapScalp(_PctStop):
    timeframe = "5m"
    startup_candle_count = 300

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["ema8"] = ta.EMA(df, timeperiod=8)
        df["rsi3"] = ta.RSI(df, timeperiod=3)
        tp = (df["high"] + df["low"] + df["close"]) / 3
        day = df["date"].dt.floor("D")
        df["vwap"] = (tp * df["volume"]).groupby(day).cumsum() / df["volume"].groupby(day).cumsum()
        df["ext"] = (df["close"] / df["vwap"] - 1).abs()
        return df

    def populate_entry_trend(self, df, metadata):
        near = df["ext"] < 0.015
        df.loc[near & (df["close"] > df["vwap"]) & (df["close"] > df["ema8"]) & (df["rsi3"] < 30), "enter_long"] = 1
        df.loc[near & (df["close"] < df["vwap"]) & (df["close"] < df["ema8"]) & (df["rsi3"] > 70), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["rsi3"] > 50, "exit_long"] = 1
        df.loc[df["rsi3"] < 50, "exit_short"] = 1
        return df


class _Flip(RiskBase):
    """Random entries, for checking the test harness at each timeframe."""
    RATE = 0.01

    def populate_indicators(self, df, metadata):
        import zlib
        df = self._base(df)
        rng = np.random.RandomState(zlib.crc32((metadata["pair"] + self.timeframe).encode()) % (2**31))
        df["coin"] = rng.random(len(df))
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["coin"] < self.RATE, "enter_long"] = 1
        df.loc[df["coin"] > 1 - self.RATE, "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df


class Flip1h(_Flip):          # matched to the 1h momentum rungs: 5 ATR stop, 1R target
    timeframe = "1h"
    STOP_ATR = 5.0
    TRAIL = False
    REWARD_R = 1.0


class Flip15m(_Flip):
    timeframe = "15m"
    STOP_ATR = 5.0
    TRAIL = False
    REWARD_R = 1.0
    RATE = 0.004


class Flip5m(_PctStop, _Flip):  # matched to VwapScalp: 0.3% stop, random exit after the stop or 0.3% gain
    timeframe = "5m"
    RATE = 0.004

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        move = (trade.open_rate - current_rate) if trade.is_short else (current_rate - trade.open_rate)
        return "take_profit" if move >= trade.open_rate * self.STOP_PCT else None


class VwapSnap(VwapScalp):
    """VwapScalp as written never triggers (price above EMA(8) with RSI(3) under 30 does not occur
    on one candle). This is the workable reading: RSI(3) dipped under 30 in the previous three
    candles and price has closed back above EMA(8) and VWAP. Mirror for shorts."""

    def populate_entry_trend(self, df, metadata):
        near = df["ext"] < 0.015
        dip = (df["rsi3"] < 30).rolling(3).max().shift(1) == 1
        pop = (df["rsi3"] > 70).rolling(3).max().shift(1) == 1
        df.loc[near & dip & (df["close"] > df["vwap"]) & (df["close"] > df["ema8"]), "enter_long"] = 1
        df.loc[near & pop & (df["close"] < df["vwap"]) & (df["close"] < df["ema8"]), "enter_short"] = 1
        return df
