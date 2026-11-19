"""
Candidate futures strategies sharing one risk model (RiskBase):
  - every trade risks RISK_PER_TRADE of the account (position sized from the stop distance)
  - stop = STOP_ATR x ATR, on the exchange; trails behind price if TRAIL is True
  - optional take profit at REWARD_R x the stop distance
  - leverage capped at MAX_LEVERAGE, isolated margin
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class RiskBase(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True

    RISK_PER_TRADE = 0.01
    MAX_LEVERAGE = 3.0
    STOP_ATR = 3.0
    TRAIL = True
    REWARD_R: Optional[float] = None

    startup_candle_count = 300
    process_only_new_candles = True
    minimal_roi = {"0": 100}
    stoploss = -0.40                 # fallback only; the ATR stop is the real one
    use_custom_stoploss = True
    use_exit_signal = True

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {"method": "StoplossGuard", "lookback_period": 1440, "trade_limit": 4,
             "stop_duration": 720, "only_per_pair": False},
            {"method": "MaxDrawdown", "lookback_period": 2880, "trade_limit": 4,
             "stop_duration": 1440, "max_allowed_drawdown": 0.08},
        ]

    # ------------------------------------------------------------------ control panel
    # Settings come from user_data/control.json (written by the dashboard). They are only
    # read while the bot is running (paper or live); backtests always use the class defaults.
    CONTROL_DEFAULTS = {
        "auto_trading": True,      # may the bot open trades on its own signals
        "sides": "both",           # both | long | short (bot trades only)
        "risk_pct": 1.0,           # % of account risked per trade
        "max_bot_trades": 4,       # open trades the bot may hold (manual trades not counted)
        "daily_target_pct": 0.0,   # stop for the day at +X% (0 = off)
        "daily_loss_pct": 0.0,     # stop for the day at -X% (0 = off)
        "target_unit": "pct",      # pct | usdt: which pair of daily limits applies
        "daily_target_usdt": 0.0,  # stop for the day at +X USDT (0 = off)
        "daily_loss_usdt": 0.0,    # stop for the day at -X USDT (0 = off)
        "on_lock": "close_all",    # close_all | stop_new
        "override_day": "",        # YYYY-MM-DD: ignore the daily stop on this UTC day
    }
    _ctl_cache: dict = {}
    _ctl_mtime: float = -1.0
    _day_state: dict = {}
    _last_rate: dict = {}

    def _running_live(self) -> bool:
        return self.config.get("runmode") in (RunMode.DRY_RUN, RunMode.LIVE)

    def _dir(self) -> Path:
        return Path(self.config.get("user_data_dir", "user_data"))

    def _ctl(self, key: str):
        return self._ctl_cache.get(key, self.CONTROL_DEFAULTS[key])

    @staticmethod
    def _manual(tag: Optional[str]) -> Optional[dict]:
        """Manual trades carry their stop and target in the entry tag: manual|sl=..|tp=..|risk=.."""
        if not tag or not tag.startswith("manual"):
            return None
        out = {}
        for part in tag.split("|")[1:]:
            k, _, v = part.partition("=")
            try:
                out[k] = float(v)
            except ValueError:
                pass
        return out

    def _load_control(self) -> None:
        f = self._dir() / "control.json"
        try:
            m = f.stat().st_mtime
        except OSError:
            self._ctl_cache, self._ctl_mtime = {}, -1.0
            return
        if m == self._ctl_mtime:
            return
        try:
            raw = json.loads(f.read_text())
            self._ctl_cache = {k: raw[k] for k in self.CONTROL_DEFAULTS if k in raw}
            self._ctl_mtime = m
        except (OSError, ValueError):
            pass                                  # keep the last good settings

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        if not self._running_live():
            return
        self._load_control()
        self._update_day(current_time)

    def _update_day(self, now: datetime) -> None:
        """Track today's profit (UTC day, closed + open trades) and apply the daily stop."""
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        f = self._dir() / "control_state.json"
        st = self._day_state
        if not st:
            try:
                st = json.loads(f.read_text())
            except (OSError, ValueError):
                st = {}
        open_trades = Trade.get_trades_proxy(is_open=True)
        if any(t.id not in self._last_rate for t in open_trades):
            self._day_state = st
            return                                # wait until every open trade has a price
        unreal = sum(t.calc_profit(self._last_rate[t.id]) for t in open_trades)
        closed = float(Trade.get_total_closed_profit() or 0.0)
        if self.config.get("dry_run"):
            equity = float(self.config.get("dry_run_wallet", 0)) + closed + unreal
        else:
            equity = float(self.wallets.get_total(self.config["stake_currency"])) + unreal
        try:                                      # trades the user placed from the desk
            equity += float(json.loads((self._dir() / "manual_state.json").read_text())["equity_delta"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if st.get("day") != day or not st.get("start_equity"):
            st = {"day": day, "start_equity": equity, "locked": None}
        pnl = equity - st["start_equity"]
        pct = 100.0 * pnl / st["start_equity"] if st["start_equity"] else 0.0
        if self._ctl("override_day") == day:
            st["locked"] = None
        elif not st.get("locked"):
            st["locked"] = self.day_lock(pnl, pct, self._ctl)
        st.update(equity=round(equity, 4), pnl=round(pnl, 4), pnl_pct=round(pct, 4),
                  open_pnl=round(unreal, 4), updated=now.astimezone(timezone.utc).isoformat())
        self._day_state = st
        try:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            os.replace(tmp, f)
        except OSError:
            pass

    @staticmethod
    def day_lock(pnl: float, pct: float, ctl) -> Optional[str]:
        """Which daily limit today's result has reached, if any. Limits of 0 are off.
        Percent limits are measured against the account at the start of the UTC day."""
        if ctl("target_unit") == "usdt":
            value, target, loss = pnl, float(ctl("daily_target_usdt")), float(ctl("daily_loss_usdt"))
        else:
            value, target, loss = pct, float(ctl("daily_target_pct")), float(ctl("daily_loss_pct"))
        if target > 0 and value >= target:
            return "daily_target"
        if loss > 0 and value <= -loss:
            return "daily_loss"
        return None

    def _locked(self) -> Optional[str]:
        return self._day_state.get("locked") if self._running_live() else None

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time,
                            entry_tag, side, **kwargs) -> bool:
        if not self._running_live():
            return True
        if self._locked():
            return False                          # daily stop reached: no new trades today
        if self._manual(entry_tag) is not None:
            return True
        if not self._ctl("auto_trading"):
            return False
        sides = self._ctl("sides")
        if sides != "both" and sides != side:
            return False
        bot_open = sum(1 for t in Trade.get_trades_proxy(is_open=True)
                       if self._manual(t.enter_tag) is None)
        return bot_open < int(self._ctl("max_bot_trades"))

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force,
                           exit_reason, current_time, **kwargs) -> bool:
        # The strategy's own exit signal must not close a trade the user opened by hand.
        if exit_reason == "exit_signal" and self._manual(trade.enter_tag) is not None:
            return False
        return True

    def _last_atr(self, pair: str, before: Optional[datetime] = None) -> Optional[float]:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None
        if before is not None:
            cut = df.loc[df["date"] <= before]
            df = cut if not cut.empty else df
        atr = df.iloc[-1]["atr"]
        return float(atr) if atr and atr == atr else None

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage,
                 entry_tag, side, **kwargs) -> float:
        return min(self.MAX_LEVERAGE, max_leverage)

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake,
                            max_stake, leverage, entry_tag, side, **kwargs) -> float:
        if self.wallets is None:
            return proposed_stake
        risk = self.RISK_PER_TRADE
        if self._running_live():
            risk = float(self._ctl("risk_pct")) / 100.0
        man = self._manual(entry_tag)
        if man is not None and man.get("sl"):
            stop_pct = abs(current_rate - man["sl"]) / current_rate
            risk = man.get("risk", risk * 100.0) / 100.0
        else:
            atr = self._last_atr(pair)
            if atr is None:
                return proposed_stake
            stop_pct = self.STOP_ATR * atr / current_rate
        if stop_pct <= 0:
            return proposed_stake
        notional = self.wallets.get_total_stake_amount() * risk / stop_pct
        return min(notional / leverage, max_stake)

    def _stop_distance(self, pair: str, trade: Trade) -> Optional[float]:
        dist = trade.get_custom_data("stop_dist")
        if dist is None:
            atr = self._last_atr(pair, trade.open_date_utc)
            if atr is None:
                return None
            dist = atr * self.STOP_ATR
            trade.set_custom_data("stop_dist", dist)
        return float(dist)

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit,
                        after_fill, **kwargs) -> Optional[float]:
        self._last_rate[trade.id] = current_rate
        man = self._manual(trade.enter_tag)
        if man is not None and man.get("sl"):
            return stoploss_from_absolute(man["sl"], current_rate,
                                          is_short=trade.is_short, leverage=trade.leverage)
        dist = self._stop_distance(pair, trade)
        if dist is None:
            return None
        if self.TRAIL:
            # Trail from the last CLOSED candle, not the intrabar extreme. Backtests otherwise
            # raise the stop to a candle's high and then test that same candle's low against it.
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            ref = float(df.iloc[-1]["close"]) if df is not None and not df.empty else trade.open_rate
        else:
            ref = trade.open_rate
        stop_price = ref + dist if trade.is_short else ref - dist
        return stoploss_from_absolute(stop_price, current_rate,
                                      is_short=trade.is_short, leverage=trade.leverage)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        self._last_rate[trade.id] = current_rate
        lock = self._locked()
        if lock and self._ctl("on_lock") == "close_all":
            return lock
        man = self._manual(trade.enter_tag)
        if man is not None:
            tp = man.get("tp")
            if tp and ((trade.is_short and current_rate <= tp) or
                       (not trade.is_short and current_rate >= tp)):
                return "manual_take_profit"
            return None
        if self.REWARD_R is None:
            return None
        dist = self._stop_distance(pair, trade)
        if dist is None:
            return None
        move = (trade.open_rate - current_rate) if trade.is_short else (current_rate - trade.open_rate)
        return "take_profit" if move >= dist * self.REWARD_R else None

    def _base(self, df: DataFrame) -> DataFrame:
        df["atr"] = ta.ATR(df, timeperiod=14)
        df["adx"] = ta.ADX(df, timeperiod=14)
        df["rsi"] = ta.RSI(df, timeperiod=14)
        df["ema50"] = ta.EMA(df, timeperiod=50)
        df["ema100"] = ta.EMA(df, timeperiod=100)
        df["ema200"] = ta.EMA(df, timeperiod=200)
        return df


class DonchianTrend(RiskBase):
    """Breakout: buy a new 2-day high in an uptrend, sell a new 2-day low in a downtrend."""
    timeframe = "1h"
    STOP_ATR = 5.0
    TRAIL = False        # the 24h channel exit does the trailing; this is a catastrophe stop

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["hi"] = df["high"].rolling(48).max().shift(1)
        df["lo"] = df["low"].rolling(48).min().shift(1)
        df["xhi"] = df["high"].rolling(24).max().shift(1)
        df["xlo"] = df["low"].rolling(24).min().shift(1)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["close"] > df["hi"]) & (df["close"] > df["ema200"]) & (df["adx"] > 20), "enter_long"] = 1
        df.loc[(df["close"] < df["lo"]) & (df["close"] < df["ema200"]) & (df["adx"] > 20), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] < df["xlo"], "exit_long"] = 1
        df.loc[df["close"] > df["xhi"], "exit_short"] = 1
        return df


class TSMomentum(RiskBase):
    """Slow momentum: follow the 20-day direction on 4h candles."""
    timeframe = "4h"
    STOP_ATR = 5.0
    TRAIL = False        # the momentum-flip exit does the work; this is a catastrophe stop

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["roc"] = df["close"] / df["close"].shift(120) - 1
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["roc"] > 0.05) & (df["close"] > df["ema100"]), "enter_long"] = 1
        df.loc[(df["roc"] < -0.05) & (df["close"] < df["ema100"]), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[(df["roc"] < 0) | (df["close"] < df["ema100"]), "exit_long"] = 1
        df.loc[(df["roc"] > 0) | (df["close"] > df["ema100"]), "exit_short"] = 1
        return df


class MeanRevert(RiskBase):
    """Fade stretched moves when there is no strong trend; exit at the average."""
    timeframe = "1h"
    STOP_ATR = 2.5
    TRAIL = False

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        mid = df["close"].rolling(20).mean()
        sd = df["close"].rolling(20).std()
        df["bb_mid"], df["bb_up"], df["bb_lo"] = mid, mid + 2 * sd, mid - 2 * sd
        return df

    def populate_entry_trend(self, df, metadata):
        calm = df["adx"] < 25
        df.loc[calm & (df["close"] < df["bb_lo"]) & (df["rsi"] < 30), "enter_long"] = 1
        df.loc[calm & (df["close"] > df["bb_up"]) & (df["rsi"] > 70), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] > df["bb_mid"], "exit_long"] = 1
        df.loc[df["close"] < df["bb_mid"], "exit_short"] = 1
        return df


class PullbackRunner(RiskBase):
    """Trend pullback on 1h, no fixed target: a trailing stop lets winners run."""
    timeframe = "1h"
    STOP_ATR = 3.0
    TRAIL = True

    def populate_indicators(self, df, metadata):
        return self._base(df)

    def populate_entry_trend(self, df, metadata):
        up = (df["close"] > df["ema200"]) & (df["ema50"] > df["ema200"]) & (df["adx"] > 20)
        dn = (df["close"] < df["ema200"]) & (df["ema50"] < df["ema200"]) & (df["adx"] > 20)
        df.loc[up & (df["rsi"] > 45) & (df["rsi"].shift(1) <= 45), "enter_long"] = 1
        df.loc[dn & (df["rsi"] < 55) & (df["rsi"].shift(1) >= 55), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df


class _CoinFlip(RiskBase):
    """Null test: random entries. A fair harness should give profit factor ~1.0 at zero fees."""
    timeframe = "1h"

    def populate_indicators(self, df, metadata):
        import numpy as np, zlib
        df = self._base(df)
        rng = np.random.RandomState(zlib.crc32(metadata["pair"].encode()) % (2**31))
        df["coin"] = rng.random(len(df))
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["coin"] < 0.03, "enter_long"] = 1
        df.loc[df["coin"] > 0.97, "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df


class CoinFlipTrail(_CoinFlip):
    STOP_ATR = 3.0
    TRAIL = True


class CoinFlipFixed(_CoinFlip):
    STOP_ATR = 3.0
    TRAIL = False
    REWARD_R = 1.0


class TurtleDaily(RiskBase):
    """Classic daily breakout: enter on a 20-day high/low, exit on the opposite 10-day extreme."""
    timeframe = "1d"
    STOP_ATR = 3.0
    TRAIL = False
    startup_candle_count = 210

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["hi"] = df["high"].rolling(20).max().shift(1)
        df["lo"] = df["low"].rolling(20).min().shift(1)
        df["xhi"] = df["high"].rolling(10).max().shift(1)
        df["xlo"] = df["low"].rolling(10).min().shift(1)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["close"] > df["hi"], "enter_long"] = 1
        df.loc[df["close"] < df["lo"], "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] < df["xlo"], "exit_long"] = 1
        df.loc[df["close"] > df["xhi"], "exit_short"] = 1
        return df


class DailyMomentum(RiskBase):
    """Daily trend: 30-day direction plus price vs its 50-day average."""
    timeframe = "1d"
    STOP_ATR = 3.0
    TRAIL = False
    startup_candle_count = 210

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        df["roc"] = df["close"] / df["close"].shift(30) - 1
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["roc"] > 0) & (df["close"] > df["ema50"]), "enter_long"] = 1
        df.loc[(df["roc"] < 0) & (df["close"] < df["ema50"]), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] < df["ema50"], "exit_long"] = 1
        df.loc[df["close"] > df["ema50"], "exit_short"] = 1
        return df
