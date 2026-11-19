"""
Strategy lab (2026-11-19): one paper profile per strategy, each in its own freqtrade dry-run
instance with its own wallet and database (see lab/profiles.json and lab.sh).

Lab profiles do not read the desk's control.json: risk is fixed at 1% per trade and the desk's
daily limits do not apply, so a profile's paper record matches what its backtest measures.
"""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from candidates import DailyMomentum, RiskBase, TSMomentum


class Lab:
    """Mixin: ignore the desk's control panel (fixed class risk, no daily lock, no shared state file)."""

    def _running_live(self) -> bool:
        return False


class LabTSMomentum(Lab, TSMomentum):
    """The swing bot's rule, run clean: fixed 1% risk, four trades at most, no desk Rules."""


class LabDailyMomentum(Lab, DailyMomentum):
    """Daily trend: 30-day direction plus price vs its 50-day average (passed the 2025-26 holdout)."""


# ---------------------------------------------------------------------------------------------
_FUND_CACHE: dict = {}


def funding_history(pair: str, data_dir: Path, live: bool) -> pd.DataFrame:
    """8-hourly funding rates for a pair: from the downloaded file in backtests, from Binance's
    public endpoint (cached for an hour) while running."""
    name = pair.replace("/", "_").replace(":", "_")
    if not live:
        if pair not in _FUND_CACHE:
            f = data_dir / "futures" / f"{name}-1h-funding_rate.feather"
            _FUND_CACHE[pair] = pd.read_feather(f)[["date", "funding_rate"]]
        return _FUND_CACHE[pair]
    hit = _FUND_CACHE.get(pair)
    if hit is not None and time.time() - hit[0] < 3600:
        return hit[1]
    sym = pair.split(":")[0].replace("/", "")
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&limit=1000"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            rows = json.loads(r.read())
        df = pd.DataFrame({
            "date": pd.to_datetime([x["fundingTime"] for x in rows], unit="ms", utc=True).floor("h"),
            "funding_rate": [float(x["fundingRate"]) for x in rows]})
        _FUND_CACHE[pair] = (time.time(), df)
        return df
    except Exception:
        if hit is not None:
            return hit[1]
        raise


class LabFundingTrend(Lab, TSMomentum):
    """TSMomentum that stands aside when its own side is crowded.

    Crowding is read from funding: the 7-day average funding rate compared with the same coin's
    previous 90 days. No new longs while it is in the top fifth (longs are paying up to hold),
    no new shorts while it is in the bottom fifth. Exits are TSMomentum's."""
    FUND_AVG = 21        # funding payments in the average (7 days)
    FUND_LOOK = 270      # payments in the comparison window (90 days)
    FUND_Q = 0.8

    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        live = self.config.get("runmode").value in ("dry_run", "live")
        try:
            f = funding_history(metadata["pair"], Path(self.config["datadir"]), live).copy()
        except Exception:
            df["crowd_long"] = True          # no funding data: take no new trades
            df["crowd_short"] = True
            return df
        f["favg"] = f["funding_rate"].rolling(self.FUND_AVG).mean()
        past = f["favg"].shift(1).rolling(self.FUND_LOOK)
        f["crowd_long"] = f["favg"] > past.quantile(self.FUND_Q)
        f["crowd_short"] = f["favg"] < past.quantile(1 - self.FUND_Q)
        f["known"] = f["favg"].notna() & past.count().ge(self.FUND_LOOK)
        f = f[["date", "favg", "crowd_long", "crowd_short", "known"]].sort_values("date")
        f["date"] = f["date"].astype(df["date"].dtype)
        out = pd.merge_asof(df[["date"]], f, on="date", direction="backward")
        known = out["known"].fillna(False).astype(bool).values
        df["favg"] = out["favg"].values
        df["crowd_long"] = np.where(known, out["crowd_long"].fillna(True).astype(bool).values, True)
        df["crowd_short"] = np.where(known, out["crowd_short"].fillna(True).astype(bool).values, True)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["roc"] > 0.05) & (df["close"] > df["ema100"]) & ~df["crowd_long"], "enter_long"] = 1
        df.loc[(df["roc"] < -0.05) & (df["close"] < df["ema100"]) & ~df["crowd_short"], "enter_short"] = 1
        return df


# ---------------------------------------------------------------------------------------------
class LabRelStrength(Lab, RiskBase):
    """Market-neutral relative strength on daily candles.

    Each day the six coins are ranked by their 30-day return. Long the top two, short the bottom
    two; a position is closed when its coin leaves the top (bottom) three. It earns from the gap
    between strong and weak coins, not from the market's direction."""
    timeframe = "1d"
    STOP_ATR = 3.0
    TRAIL = False
    startup_candle_count = 210
    LOOK = 30

    def informative_pairs(self):
        return [(p, self.timeframe) for p in self.dp.current_whitelist()]

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        rocs = {}
        for p in self.dp.current_whitelist():
            d = self.dp.get_pair_dataframe(p, self.timeframe)
            rocs[p] = (d["close"] / d["close"].shift(self.LOOK) - 1).set_axis(d["date"])
        table = pd.DataFrame(rocs)
        n = table.notna().sum(axis=1)
        rank = table.rank(axis=1, ascending=False)          # 1 = strongest
        me = metadata["pair"]
        r = rank[me].reindex(df["date"]).values
        k = n.reindex(df["date"]).values
        full = k >= 6
        df["rs_rank"] = r
        df["rs_top"] = full & (r <= 2)
        df["rs_bot"] = full & (r >= k - 1)
        df["rs_not_top"] = ~full | (r > 3)
        df["rs_not_bot"] = ~full | (r < k - 2)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["rs_top"], "enter_long"] = 1
        df.loc[df["rs_bot"], "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["rs_not_top"], "exit_long"] = 1
        df.loc[df["rs_not_bot"], "exit_short"] = 1
        return df


# ---------------------------------------------------------------------------------------------
class LabCoinFlip(Lab, RiskBase):
    """Control profile: random entries on 4h candles with the trend bot's stop (5 x ATR), closed
    after five days. It shows what luck alone looks like over the same weeks. The random number
    is a hash of coin and candle time, so it is the same in a backtest and while running."""
    timeframe = "4h"
    STOP_ATR = 5.0
    TRAIL = False
    HOLD_CANDLES = 30
    P = 0.02
    SALT = ""            # other values give other random sequences (research/lab_seeds.py)

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        pair = metadata["pair"]
        df["coin"] = [int(hashlib.md5(f"{self.SALT}{pair}{d.value}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                      for d in df["date"]]
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["coin"] < self.P, "enter_long"] = 1
        df.loc[df["coin"] > 1 - self.P, "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.HOLD_CANDLES * 4 * 3600:
            return "time_exit"
        return None


# ---------------------------------------------------------------------------------------------
RATIO_DIR = Path(__file__).resolve().parents[2] / "lab" / "ratio"
_RATIO_CACHE: dict = {}
RATIO_KEEP_DAYS = 130
RATIO_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"


def _ratio_archive(coin: str, data_dir: Path) -> pd.Series:
    z = np.load(data_dir.parent / "oi" / f"{coin}_5m.npz")
    s = pd.Series(z["ls"], index=z["t"].astype(np.int64))
    return s[s > 0]


def ratio_history(pair: str, data_dir: Path, live: bool) -> pd.Series:
    """All-accounts long/short ratio every 5 minutes (index: unix seconds).

    Backtest: the downloaded archive. Running: a file per coin in lab/ratio, started from the
    archive and extended from Binance's public endpoint (which keeps only 30 days) every 4 minutes."""
    coin = pair.split("/")[0]
    if not live:
        if coin not in _RATIO_CACHE:
            _RATIO_CACHE[coin] = _ratio_archive(coin, data_dir)
        return _RATIO_CACHE[coin]
    hit = _RATIO_CACHE.get(coin)
    if hit is not None and time.time() - hit[0] < 240:
        return hit[1]
    f = RATIO_DIR / f"{coin}.csv"
    if hit is not None:
        s = hit[1]
    elif f.exists():
        d = pd.read_csv(f)
        s = pd.Series(d["ls"].to_numpy(), index=d["t"].to_numpy().astype(np.int64))
    else:
        try:
            s = _ratio_archive(coin, data_dir)
        except OSError:                        # no archive: start from the endpoint's 30 days
            s = pd.Series(dtype=float, index=pd.Index([], dtype=np.int64))
    now = int(time.time())
    s = s[s.index >= now - RATIO_KEEP_DAYS * 86400]
    start = max(int(s.index.max()) + 300 if len(s) else 0, now - 29 * 86400)
    try:
        while start < now - 300:
            end = min(start + 499 * 300, now)          # without endTime the endpoint returns the latest rows
            url = (f"{RATIO_URL}?symbol={coin}USDT&period=5m&limit=500"
                   f"&startTime={start * 1000}&endTime={end * 1000}")
            with urllib.request.urlopen(url, timeout=10) as r:
                rows = json.loads(r.read())
            if rows:
                new = pd.Series([float(x["longShortRatio"]) for x in rows],
                                index=[int(x["timestamp"]) // 1000 for x in rows])
                s = pd.concat([s, new[new > 0]])
                s = s[~s.index.duplicated(keep="last")].sort_index()
            start = end + 300
        RATIO_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        pd.DataFrame({"t": s.index, "ls": s.to_numpy()}).to_csv(tmp, index=False)
        tmp.replace(f)
    except Exception:
        pass                                   # keep what we have; a stale reading gives no signal
    _RATIO_CACHE[coin] = (time.time(), s)
    return s


class LabCrowdFade(Lab, RiskBase):
    """Trade against the crowd of futures accounts.

    Reading: accounts net long / accounts net short (all accounts), taken at least 5 minutes before
    each 1h close and ranked against the coin's own previous 90 days. Long when it is in the lowest
    5% (the crowd is unusually short), short when in the highest 5%. Stop 6 x ATR(1h); closed after
    24 hours. Passed training and the 2025-26 held-back period (docs/long-short-ratio-test.md)."""
    timeframe = "1h"
    STOP_ATR = 6.0
    TRAIL = False
    startup_candle_count = 2400
    HOLD_H = 24
    RANK = 2160

    def populate_indicators(self, df, metadata):
        df = self._base(df)
        live = self.config.get("runmode").value in ("dry_run", "live")
        try:
            s = ratio_history(metadata["pair"], Path(self.config["datadir"]), live)
            grid = s.reindex(np.arange(s.index.min(), s.index.max() + 300, 300)).ffill(limit=2)
            now = df["date"].dt.as_unit("s").astype("int64").to_numpy() + 3600 - 300
            df["ls"] = grid.reindex(now).to_numpy()
        except Exception:
            df["ls"] = np.nan                  # no reading: no new trades
        past = df["ls"].shift(1).rolling(self.RANK, min_periods=1500)
        df["ls_lo"], df["ls_hi"] = past.quantile(0.05), past.quantile(0.95)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["ls"] <= df["ls_lo"], "enter_long"] = 1
        df.loc[df["ls"] >= df["ls_hi"], "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.HOLD_H * 3600:
            return "time_exit"
        return None
