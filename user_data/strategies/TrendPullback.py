"""
TrendPullback - starter futures strategy (long and short).

Idea: trade in the direction of the trend, enter when a pullback ends.
  Long : price above EMA200, EMA50 above EMA200, ADX shows a trend,
         RSI dipped and crosses back up through RSI_LONG.
  Short: the mirror image.

Every trade gets:
  - a stop loss  = STOP_ATR x ATR away from entry (placed on the exchange)
  - a take profit = REWARD_R x that stop distance
  - a hard fallback stop (`stoploss`) in case the ATR stop cannot be read

This is a starting point to test the plumbing, not a proven money-maker.
"""
from datetime import datetime
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute
import freqtrade.vendor.qtpylib.indicators as qtpylib


class TrendPullback(IStrategy):
    INTERFACE_VERSION = 3

    # --- settings you can change -------------------------------------
    timeframe = "15m"
    MAX_LEVERAGE = 3.0      # hard cap, whatever the exchange allows
    STOP_ATR = 2.0          # stop distance in ATRs
    REWARD_R = 2.0          # take profit at this multiple of the stop distance
    RSI_LONG = 40
    RSI_SHORT = 60
    ADX_MIN = 20
    # -----------------------------------------------------------------

    can_short = True
    startup_candle_count = 250
    process_only_new_candles = True

    minimal_roi = {"0": 100}        # disabled: exits come from stop / take profit
    stoploss = -0.15                # fallback: -15% on margin = -5% price at 3x
    use_custom_stoploss = True
    trailing_stop = False
    use_exit_signal = True          # needed for custom_exit (take profit) to run

    @property
    def protections(self):
        return [
            # wait 2 candles after any exit before re-entering that pair
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            # 3 stop-outs within 24h -> stop all trading for 12h
            {"method": "StoplossGuard", "lookback_period_candles": 96,
             "trade_limit": 3, "stop_duration_candles": 48, "only_per_pair": False},
            # account down 8% over 48h -> stop all trading for 24h
            {"method": "MaxDrawdown", "lookback_period_candles": 192,
             "trade_limit": 4, "stop_duration_candles": 96, "max_allowed_drawdown": 0.08},
        ]

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["ema50"] = ta.EMA(df, timeperiod=50)
        df["ema200"] = ta.EMA(df, timeperiod=200)
        df["rsi"] = ta.RSI(df, timeperiod=14)
        df["adx"] = ta.ADX(df, timeperiod=14)
        df["atr"] = ta.ATR(df, timeperiod=14)
        return df

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        trending = (df["adx"] > self.ADX_MIN) & (df["volume"] > 0)
        df.loc[
            trending
            & (df["close"] > df["ema200"]) & (df["ema50"] > df["ema200"])
            & qtpylib.crossed_above(df["rsi"], self.RSI_LONG),
            ["enter_long", "enter_tag"],
        ] = (1, "pullback_long")
        df.loc[
            trending
            & (df["close"] < df["ema200"]) & (df["ema50"] < df["ema200"])
            & qtpylib.crossed_below(df["rsi"], self.RSI_SHORT),
            ["enter_short", "enter_tag"],
        ] = (1, "pullback_short")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        return df

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return min(self.MAX_LEVERAGE, max_leverage)

    # -- stop loss / take profit ---------------------------------------
    def _stop_distance(self, pair: str, trade: Trade) -> Optional[float]:
        """Price distance of the stop, fixed once from the ATR at entry."""
        dist = trade.get_custom_data("stop_dist")
        if dist is None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is None or df.empty:
                return None
            candles = df.loc[df["date"] <= trade.open_date_utc]
            atr = (candles if not candles.empty else df).iloc[-1]["atr"]
            if not atr or atr != atr:      # zero or NaN
                return None
            dist = float(atr) * self.STOP_ATR
            trade.set_custom_data("stop_dist", dist)
        return float(dist)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        dist = self._stop_distance(pair, trade)
        if dist is None:
            return None                     # keep the fallback stop
        stop_price = trade.open_rate + dist if trade.is_short else trade.open_rate - dist
        return stoploss_from_absolute(stop_price, current_rate,
                                      is_short=trade.is_short, leverage=trade.leverage)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        dist = self._stop_distance(pair, trade)
        if dist is None:
            return None
        target = dist * self.REWARD_R
        move = (trade.open_rate - current_rate) if trade.is_short else (current_rate - trade.open_rate)
        if move >= target:
            return "take_profit"
        return None
